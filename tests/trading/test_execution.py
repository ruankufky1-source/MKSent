import asyncio
import time
from decimal import Decimal as D

import pytest

from sentinela.trading import store
from sentinela.trading.broker import BrokerError, GuardConfig, GuardedBroker, OrderRequest
from sentinela.trading.execution import (
    APPROVAL_TTL_S, BROKER_TIMEOUT_S, Execution,
)
from sentinela.trading.bus import HANDLER_TIMEOUT_S
from sentinela.trading.halts import INTEGRITY
from sentinela.trading.simbroker import SimBroker
from sentinela.trading.store import StateWriteError
from tests.trading.exec_env import Rig, run


def cid_of(rec, ticker="AAA", side="buy"):
    return f"sm-{rec['seq']}-{ticker}-{side}"


def lifecycle(rig, corr):
    return [r["type"] for r in rig.log() if r["correlation_id"] == corr
            and r["agent"] in ("risk", "execution")]


def closed(rig, approval_id=None):
    out = [r["payload"] for r in rig.log("OrderClosed")]
    return [p for p in out if approval_id is None or p["approval_id"] == approval_id]


def fills(rig, approval_id=None):
    out = [r["payload"] for r in rig.log("Fill")]
    return [p for p in out if approval_id is None or p["approval_id"] == approval_id]


def test_constants_leave_room_for_submit_and_lookup_inside_the_handler_timeout():
    assert APPROVAL_TTL_S == 30
    assert 2 * BROKER_TIMEOUT_S < HANDLER_TIMEOUT_S


# --- caminho saudável (controle positivo de todos os critérios) -----------------------------

def test_healthy_buy_is_sent_filled_closed_and_explainable(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        rec = r.approve(seq=1, corr="sig-1")
        await r.settle()
        await r.stop()
        return rec

    rec = run(main())
    r.audit.close()
    assert lifecycle(r, "sig-1") == ["OrderApproved", "OrderIntent", "OrderSubmitted", "Fill",
                                     "OrderClosed"]
    assert cid_of(rec) in r.sim.orders and r.sim.submit_calls == 1
    assert D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 10
    assert r.risk_state()["pending"] == {} and r.rules() == []
    intent = r.log("OrderIntent")[0]
    assert intent["payload"]["client_order_id"] == cid_of(rec)
    assert intent["causation_id"] == rec["seq"] and intent["agent"] == "execution"
    assert r.state()["orders"][cid_of(rec)]["state"] == "closed"


# --- critério 1: Guard no caminho do Execution ----------------------------------------------

def test_execution_only_accepts_a_guarded_broker(tmp_path, clock):
    r = Rig(tmp_path, clock)
    with pytest.raises(TypeError):
        Execution(r.bus, r.sim, tmp_path / "x.json", clock, positions=dict,
                  kill_path=tmp_path / "PARAR", first_run=True)


def test_guard_ceiling_refusal_through_the_execution_halts_and_releases(tmp_path, clock):
    r = Rig(tmp_path, clock, guard_cfg=GuardConfig(D("500"), D("1000"), frozenset({"AAA", "BBB"})))

    async def main():
        await r.start()
        r.forge(qtd="10", price="50", approval_id="ok", corr="ok")        # 500: passa
        await r.settle()
        assert r.sim.submit_calls == 1 and r.rules() == []
        r.forge(qtd="20", price="50", approval_id="grande", corr="grande")   # 1.000: o Risk poderia aprovar
        await r.settle()
        await r.stop()

    run(main())
    assert r.sim.submit_calls == 1   # a recusada não chegou à corretora
    assert "guard_recusou" in r.rules()
    c = closed(r, "grande")[0]
    assert c["status"] == "not_sent" and c["filled_qty"] == "0" and c["reason"] == "guard:teto_de_valor"


def test_guard_list_is_independent_of_the_risk_list(tmp_path, clock):
    r = Rig(tmp_path, clock, guard_cfg=GuardConfig(D("100000"), D("1000"), frozenset({"AAA"})))

    async def main():
        await r.start()
        r.approve(ticker="BBB", seq=1)   # o Risk permite BBB, o Guard não
        await r.settle()
        await r.stop()

    run(main())
    assert r.sim.submit_calls == 0 and "guard_recusou" in r.rules()
    assert closed(r)[0]["reason"] == "guard:ticker_fora_da_lista"
    assert r.risk_state()["pending"] == {}   # a reserva do Risk foi solta


def test_invalid_approval_payload_is_not_sent_and_halts(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        r.forge(qtd="abc", approval_id="lixo", corr="lixo")
        await r.settle()
        await r.stop()

    run(main())
    assert r.sim.submit_calls == 0 and "aprovacao_invalida" in r.rules()
    assert closed(r, "lixo")[0]["status"] == "not_sent"


# --- critério 2: idempotência ---------------------------------------------------------------

def test_same_approval_delivered_twice_is_sent_once(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        rec = r.approve(seq=1)
        await r.settle()
        await r.execution.handle(rec)   # o mesmo evento outra vez
        await r.settle()
        await r.stop()
        return rec

    rec = run(main())
    assert r.sim.submit_calls == 1 and len(r.log("OrderIntent")) == 1
    ign = [x["payload"] for x in r.log("OrderEventIgnored") if x["agent"] == "execution"]
    assert ign and ign[0]["approval_id"] == rec["payload"]["approval_id"]
    assert fills(r)[0]["qty"] == "10" and len(fills(r)) == 1


def test_two_sells_of_the_same_ticker_and_bar_get_different_ids(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        a = r.approve(side="sell", qtd="4", seq=2)   # mesma barra "b1"
        b = r.approve(side="sell", qtd="3", seq=3)
        await r.settle()
        await r.stop()
        return a, b

    a, b = run(main())
    ids = {cid_of(a, side="sell"), cid_of(b, side="sell")}
    assert len(ids) == 2 and ids <= set(r.sim.orders)
    assert r.sim.submit_calls == 3
    assert D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 3


def test_same_event_after_restart_sends_nothing_new(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def first():
        await r.start()
        rec = r.approve(seq=1)
        await r.settle()
        await r.stop()
        return rec

    rec = run(first())
    r.restart()

    async def second():
        await r.start()
        await r.execution.handle(rec)
        await r.settle()
        await r.stop()

    run(second())
    assert r.sim.submit_calls == 1 and len(r.log("OrderIntent")) == 1
    assert r.rules() == []   # a reconciliação depois do reinício saiu limpa


# --- critério 3: intenção antes do envio ----------------------------------------------------

def test_intent_is_in_the_log_and_the_file_before_the_broker_sees_the_order(tmp_path, clock):
    r = Rig(tmp_path, clock)
    seen = {}

    def spy(req):
        seen["state"] = r.state()["orders"][req.client_order_id]["state"]
        seen["types"] = [x["type"] for x in r.log()]
        seen["id"] = req.client_order_id

    r.sim.before_submit = spy

    async def main():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        await r.stop()

    run(main())
    assert seen["state"] == "intent" and seen["types"][-1] == "OrderIntent"
    assert r.log("OrderIntent")[0]["payload"]["client_order_id"] == seen["id"]


def test_intent_file_write_failure_means_no_send_and_halt(tmp_path, clock, monkeypatch):
    r = Rig(tmp_path, clock)
    real = store.atomic_write_json

    def boom(path, obj):
        if str(path).endswith("execution_state.json"):
            raise StateWriteError("disco cheio")
        return real(path, obj)

    async def main():
        await r.start()
        r.approve(seq=1)
        monkeypatch.setattr(store, "atomic_write_json", boom)
        await r.settle()
        await r.stop()

    run(main())
    assert r.sim.submit_calls == 0 and "execution_estado_nao_gravou" in r.rules()
    monkeypatch.setattr(store, "atomic_write_json", real)
    assert r.risk_state()["pending"] == {}   # a ordem foi fechada: nada ficou reservado


def test_intent_log_write_failure_means_no_send(tmp_path, clock, monkeypatch):
    r = Rig(tmp_path, clock)

    def boom(raw):
        raise OSError("disco cheio")

    async def main():
        await r.start()
        r.approve(seq=1)
        monkeypatch.setattr(r.audit, "_write", boom)
        await r.settle()
        await r.stop()

    run(main())
    assert r.sim.submit_calls == 0 and "log_falhou" in r.rules()


# --- critério 4: envio ambíguo --------------------------------------------------------------

def test_lost_response_is_resolved_by_lookup_and_never_resent(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.inject("lost_response")

    async def main():
        await r.start()
        rec = r.approve(seq=1)
        t0 = time.monotonic()
        await r.settle()
        elapsed = time.monotonic() - t0
        await r.stop()
        return rec, elapsed

    rec, elapsed = run(main())
    assert elapsed < 0.45   # o sim dorme 0,5 s: o timeout do Execution (0,05 s) é que corta
    assert r.sim.submit_calls == 1 and r.rules() == []
    assert r.log("OrderSubmitted")[0]["payload"]["outcome"] == "found_after_timeout"
    assert D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 10
    assert closed(r)[0]["status"] == "filled" and r.state()["orders"][cid_of(rec)]["state"] == "closed"


def test_timeout_before_the_order_exists_halts_keeps_the_reservation_and_never_resends(
        tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.inject("timeout")

    async def main():
        await r.start()
        rec = r.approve(seq=1)
        await r.settle()
        assert r.sim.submit_calls == 1 and r.rules() == ["envio_ambiguo"]
        assert r.state()["orders"][cid_of(rec)]["state"] == "ambiguous"
        assert len(r.risk_state()["pending"]) == 1   # sem saber, a reserva fica
        await r.execution.clear_halt(r.risk_agent, "ruan", "conferi na corretora")
        await r.settle()
        await r.stop()
        return rec

    rec = run(main())
    assert r.sim.submit_calls == 1   # depois da reconciliação continua 1
    assert r.state()["orders"][cid_of(rec)]["state"] == "not_sent"
    assert closed(r)[0]["status"] == "not_sent" and r.risk_state()["pending"] == {}
    assert r.rules() == []


def test_duplicate_id_answer_is_resolved_by_lookup_and_adopted(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.inject("duplicate")

    async def main():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        await r.stop()

    run(main())
    assert r.sim.submit_calls == 1 and r.rules() == []
    assert r.log("OrderSubmitted")[0]["payload"]["outcome"] == "duplicate_adopted"
    assert D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 10


def test_lookup_that_also_times_out_halts_and_the_order_is_adopted_after_clear(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.inject("lost_response")
    r.sim.inject_lookup("timeout")

    async def main():
        await r.start()
        rec = r.approve(seq=1)
        await r.settle()
        assert r.rules() == ["envio_ambiguo"] and r.sim.submit_calls == 1
        assert r.state()["orders"][cid_of(rec)]["state"] == "ambiguous"
        await r.execution.clear_halt(r.risk_agent, "ruan", "conferi")   # reconcilia: acha e adota
        await r.settle()
        await r.stop()
        return rec

    rec = run(main())
    assert r.sim.submit_calls == 1 and r.rules() == []
    assert D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 10
    assert len(fills(r)) == 1 and closed(r)[0]["status"] == "filled"
    assert r.log("OrderSubmitted")[-1]["payload"]["outcome"] == "adopted_on_reconcile"


# --- critério 5: validade e halt lido na hora -----------------------------------------------

@pytest.mark.parametrize("age, sent", [(29, True), (30, True), (31, False), (-1, False)])
def test_approval_age_boundaries(tmp_path, clock, age, sent):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        r.approve(seq=1)
        clock.advance(age)   # a aprovação espera na fila do Execution
        await r.settle()
        await r.stop()

    run(main())
    assert r.sim.submit_calls == (1 if sent else 0)
    if not sent:
        c = closed(r)[0]
        assert c["status"] == "expired" and c["filled_qty"] == "0"
        assert r.risk_state()["pending"] == {} and r.risk_state()["trades_today"] == 0
    else:
        assert closed(r)[0]["status"] == "filled"


def test_integrity_halt_raised_after_the_approval_stops_the_send(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        rec = r.approve(seq=1)
        r.halts.raise_("fila_cheia", INTEGRITY)
        await r.settle()
        await r.stop()
        return rec

    rec = run(main())
    assert r.sim.submit_calls == 0
    c = closed(r)[0]
    assert c["status"] == "not_sent" and c["reason"] == "halt_integridade"
    assert r.risk_state()["pending"] == {} and r.risk_state()["trades_today"] == 0
    assert r.state()["orders"][cid_of(rec)]["state"] == "not_sent"


def test_halt_raised_while_the_intent_is_written_still_stops_the_send(tmp_path, clock):
    r = Rig(tmp_path, clock)
    real = r.audit.append

    def append(**kw):
        rec = real(**kw)
        if kw["type"] == "OrderIntent":
            r.halts.raise_("fila_cheia", INTEGRITY)   # o halt chega no meio da gravação
        return rec

    r.audit.append = append

    async def main():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        await r.stop()

    run(main())
    assert r.sim.submit_calls == 0 and closed(r)[0]["reason"] == "halt_integridade"
    assert r.risk_state()["pending"] == {} and r.risk_state()["trades_today"] == 0
    assert [o["state"] for o in r.state()["orders"].values()] == ["not_sent"]


def test_stop_file_created_after_the_approval_stops_the_buy_but_not_the_sell(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        buy = r.approve(ticker="BBB", seq=2)
        sell = r.approve(side="sell", qtd="4", seq=3)
        (tmp_path / "PARAR").write_text("parar", encoding="utf-8")
        await r.settle()
        await r.stop()
        return buy, sell

    buy, sell = run(main())
    assert cid_of(buy, "BBB") not in r.sim.orders and cid_of(sell, side="sell") in r.sim.orders
    c = closed(r, buy["payload"]["approval_id"])[0]
    assert c["status"] == "not_sent" and c["reason"] == "halt_limite:kill_switch"
    assert "kill_switch" in r.rules()
    assert r.risk_state()["trades_today"] == 1   # a compra bloqueada devolveu o trade


def test_limit_halt_stops_a_buy_but_not_a_sell(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        buy = r.approve(ticker="BBB", seq=2)
        sell = r.approve(side="sell", qtd="4", seq=3)
        r.halts.raise_("kill_switch", "limit")
        await r.settle()
        await r.stop()
        return buy, sell

    buy, sell = run(main())
    assert cid_of(sell, side="sell") in r.sim.orders and cid_of(buy, "BBB") not in r.sim.orders
    c = closed(r, buy["payload"]["approval_id"])[0]
    assert c["status"] == "not_sent" and c["reason"] == "halt_limite:kill_switch"
    assert D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 6
    assert r.risk_state()["trades_today"] == 1 and r.risk_state()["pending"] == {}
    assert r.state()["orders"][cid_of(buy, "BBB")]["state"] == "not_sent"


def test_nothing_is_sent_before_the_first_reconciliation(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start(reconcile=False)
        r.approve(seq=1)
        await r.settle()
        assert r.sim.submit_calls == 0
        assert closed(r)[0]["reason"] == "sem_reconciliacao"
        assert r.risk_state()["pending"] == {} and r.risk_state()["trades_today"] == 0
        assert [o["state"] for o in r.state()["orders"].values()] == ["not_sent"]
        await r.execution.reconcile()
        r.approve(seq=2)
        await r.settle()
        await r.stop()

    run(main())
    assert r.sim.submit_calls == 1 and r.rules() == []


# --- critério 6: fills, parcial e fechamento ------------------------------------------------

def test_partial_then_complete_publishes_fills_by_delta_and_closes_last(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        rec = r.approve(price="100", seq=1)
        await r.settle()
        assert [(f["qty"], D(f["price"])) for f in fills(r)] == [("4", D("100"))]
        assert closed(r) == [] and r.risk_state()["pending"] != {}
        r.sim.advance()
        await r.settle()
        await r.stop()
        return rec

    rec = run(main())
    fs = fills(r)
    assert [(D(f["qty"]), D(f["price"])) for f in fs] == [(D(4), D(100)), (D(6), D(101))]
    types = [x["type"] for x in r.log() if x["type"] in ("Fill", "OrderClosed")]
    assert types == ["Fill", "Fill", "OrderClosed"]
    assert closed(r)[0]["filled_qty"] == "10" and closed(r)[0]["status"] == "filled"
    p = r.portfolio.view()["positions"]["AAA"]
    assert D(p["qty"]) == 10 and D(p["avg"]) == D("100.60")
    assert r.risk_state()["pending"] == {}


def test_order_closed_never_precedes_a_fill_of_the_same_order_in_200_random_orders(tmp_path, clock):
    from sentinela.trading.simbroker import SimBroker
    r = Rig(tmp_path, clock, sim=SimBroker(seed=3, partial_prob=0.10, stall_s=0.5))

    async def main():
        await r.start()
        for i in range(200):
            r.forge(qtd="10", price="1", approval_id=f"f{i}", corr=f"f{i}")
            if i % 10 == 9:
                await r.settle()
        for _ in range(3):
            r.sim.advance()
            await r.settle()
        await r.stop()

    run(main())
    log = r.log()
    last_fill, close_at = {}, {}
    for i, x in enumerate(log):
        if x["type"] == "Fill":
            last_fill[x["payload"]["approval_id"]] = i
        elif x["type"] == "OrderClosed":
            close_at[x["payload"]["approval_id"]] = i
    assert len(close_at) == 200 and all(close_at[k] > last_fill[k] for k in close_at)
    partials = [k for k in close_at if len([f for f in fills(r, k)]) == 2]
    assert 5 <= len(partials) <= 40   # a amostra teve parciais de verdade
    assert all(sum(D(f["qty"]) for f in fills(r, k)) == 10 for k in close_at)
    assert r.rules() == []


def test_order_already_filled_on_first_sight_publishes_fill_then_close(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        await r.stop()

    run(main())
    types = [x["type"] for x in r.log() if x["type"] in ("Fill", "OrderClosed")]
    assert types == ["Fill", "OrderClosed"] and fills(r)[0]["qty"] == "10"


def test_canceled_with_four_of_ten_closes_partial_and_consumes_the_trade(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        rec = r.approve(price="100", seq=1)
        await r.settle()
        r.sim.cancel(cid_of(rec))
        await r.settle()
        await r.stop()

    run(main())
    c = closed(r)[0]
    assert c["status"] == "canceled" and c["filled_qty"] == "4"
    assert r.risk_state()["trades_today"] == 1 and r.risk_state()["pending"] == {}
    assert D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 4


def test_rejected_order_closes_with_zero_and_frees_everything(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.inject("reject")

    async def main():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        await r.stop()

    run(main())
    c = closed(r)[0]
    assert c["status"] == "rejected" and c["filled_qty"] == "0"
    assert r.risk_state()["trades_today"] == 0 and r.risk_state()["pending"] == {}
    assert fills(r) == [] and r.rules() == []


@pytest.mark.parametrize("override", [
    dict(filled_qty=D("11")),                                   # 11 de 10
    dict(status="partially_filled", filled_qty=D("11")),        # 11 de 10 sem "filled" para avisar
    dict(status="partially_filled", filled_qty=D("5"), filled_avg_price=None),
    dict(filled_qty=D("5"), filled_avg_price=None),             # fill sem preço médio
    dict(status="filled", filled_qty=D("6")),                   # "filled" com menos que a quantidade
    dict(filled_qty=D("-1")),
    dict(ticker="BBB"),                                         # ordem de outro ticker
])
def test_inconsistent_broker_data_halts_and_publishes_no_fill(tmp_path, clock, override):
    r = Rig(tmp_path, clock)
    r.sim.override_next(**override)

    async def main():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        await r.stop()

    run(main())
    assert "broker_invalido" in r.rules() and fills(r) == [] and closed(r) == []


def test_filled_quantity_that_goes_down_halts(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        rec = r.approve(price="100", seq=1)
        await r.settle()
        r.sim.override(cid_of(rec), filled_qty=D("2"))
        await r.settle()
        await r.stop()

    run(main())
    assert "broker_invalido" in r.rules() and len(fills(r)) == 1


def test_failed_periodic_lookup_is_logged_without_halting(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        rec = r.approve(price="100", seq=1)
        await r.settle()
        r.sim.inject_lookup("error")
        await r.execution.poll_once()
        r.sim.advance()
        await r.settle()
        await r.stop()
        return rec

    rec = run(main())
    pf = r.log("PollFailed")
    assert len(pf) == 1 and pf[0]["payload"]["client_order_id"] == cid_of(rec)
    assert r.rules() == [] and closed(r)[0]["filled_qty"] == "10"   # a consulta seguinte funcionou


def test_exception_in_the_poll_loop_halts_the_critical_agent(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def boom():
        raise RuntimeError("bug na consulta")

    r.execution.poll_once = boom

    async def main():
        await r.start()
        task = asyncio.create_task(r.execution.run_poller(0.001))
        await asyncio.sleep(0.05)
        await task   # o laço termina sozinho depois de parar o agente
        await r.stop()

    run(main())
    assert "agente_execution_caiu" in r.rules()


# --- critério 4 (ampliado): erro de conexão é ambíguo, adoção confere o conteúdo ------------

@pytest.mark.parametrize("fault, created", [("connection_error_lost", True), ("connection_error", False)])
def test_any_submit_error_is_ambiguous_and_never_closes_the_order_directly(tmp_path, clock, fault, created):
    r = Rig(tmp_path, clock)
    r.sim.inject(fault)

    async def main():
        await r.start()
        rec = r.approve(seq=1)
        await r.settle()
        await r.stop()
        return rec

    rec = run(main())
    assert r.sim.submit_calls == 1
    if created:
        assert r.rules() == [] and closed(r)[0]["status"] == "filled"
    else:
        assert r.rules() == ["envio_ambiguo"] and closed(r) == []
        assert r.state()["orders"][cid_of(rec)]["state"] == "ambiguous"


@pytest.mark.parametrize("override", [dict(qty=D("7")), dict(ticker="BBB"), dict(side="sell")])
def test_adopting_an_order_checks_that_it_is_the_one_we_intended(tmp_path, clock, override):
    r = Rig(tmp_path, clock)
    r.sim.inject("duplicate")
    r.sim.override_next(**override)

    async def main():
        await r.start()
        rec = r.approve(seq=1)
        await r.settle()
        await r.stop()
        return rec

    rec = run(main())
    assert "broker_invalido" in r.rules() and fills(r) == [] and closed(r) == []
    assert r.state()["orders"][cid_of(rec)]["state"] == "invalid"


# --- critério 6 (ampliado) ------------------------------------------------------------------

def test_expired_with_four_of_ten_closes_partial_and_consumes_the_trade(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        rec = r.approve(price="100", seq=1)
        await r.settle()
        r.sim.expire(cid_of(rec))
        await r.settle()
        await r.stop()

    run(main())
    c = closed(r)[0]
    assert c["status"] == "expired" and c["filled_qty"] == "4"
    assert r.risk_state()["trades_today"] == 1 and r.risk_state()["pending"] == {}


def test_unknown_broker_status_halts(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.override_next(status="pending_cancel")

    async def main():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        await r.stop()

    run(main())
    assert "broker_invalido" in r.rules() and fills(r) == [] and closed(r) == []


def test_delta_price_that_comes_out_non_positive_halts_before_publishing(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        rec = r.approve(price="100", seq=1)
        await r.settle()
        # acumulado 5 @ 70 = 350, menos que os 400 já publicados: o delta teria preço negativo
        r.sim.override(cid_of(rec), filled_qty=D("5"), filled_avg_price=D("70"))
        await r.settle()
        await r.stop()

    run(main())
    assert "broker_invalido" in r.rules() and len(fills(r)) == 1


def test_open_order_keeps_being_followed_while_an_integrity_halt_is_active(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        r.approve(price="100", seq=1)
        await r.settle()
        r.halts.raise_("fila_cheia", INTEGRITY)
        r.sim.advance()
        await r.settle()
        await r.stop()

    run(main())
    assert len(fills(r)) == 2 and closed(r)[0]["filled_qty"] == "10"
    assert r.risk_state()["pending"] == {}
    assert D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 10


def test_invalid_order_is_no_longer_polled(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        rec = r.approve(price="100", seq=1)
        await r.settle()
        r.sim.override(cid_of(rec), filled_qty=D("2"))
        await r.settle()
        before = r.sim.read_calls
        r.sim.override(cid_of(rec))   # volta ao normal
        r.sim.advance()
        await r.settle()
        assert r.sim.read_calls == before   # a consulta parou; só a reconciliação mexe nela
        await r.stop()

    run(main())
    assert len(fills(r)) == 1 and closed(r) == []


def test_crash_between_publishing_the_close_and_saving_closed_publishes_one_close(
        tmp_path, clock, monkeypatch):
    r = Rig(tmp_path, clock)
    real = store.atomic_write_json

    def boom(path, obj):
        if str(path).endswith("execution_state.json") and any(
                o["state"] == "closed" for o in obj["orders"].values()):
            raise StateWriteError("queda")
        return real(path, obj)

    async def first():
        await r.start()
        r.approve(seq=1)
        monkeypatch.setattr(store, "atomic_write_json", boom)
        await r.settle()
        await r.stop()

    run(first())
    assert len(closed(r)) == 1
    monkeypatch.setattr(store, "atomic_write_json", real)
    r.restart()

    async def second():
        await r.start()
        await r.settle()
        await r.stop()

    run(second())
    assert len(closed(r)) == 1   # nenhum fechamento repetido
    assert [o["state"] for o in r.state()["orders"].values()] == ["closed"]
    assert r.risk_state()["pending"] == {}


def test_reconciliation_waits_for_a_poll_that_holds_an_older_snapshot_of_the_same_order(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        r.approve(price="100", seq=1)
        await r.settle()
        gate = r.sim.hold_next_lookup()
        a = asyncio.create_task(r.execution.poll_once())   # tira a foto "4 de 10" e fica esperando
        await asyncio.sleep(0.01)
        r.sim.advance()   # a corretora completa a ordem
        b = asyncio.create_task(r.execution.reconcile(halt=False))
        await asyncio.sleep(0.01)
        gate.set()   # a consulta velha volta com a foto antiga
        await asyncio.gather(a, b)
        await r.settle()
        await r.stop()

    run(main())
    assert r.rules() == []   # sem a trava, a foto velha seria aplicada depois da nova: "acumulado diminuiu"
    assert [f["qty"] for f in fills(r)] == ["4", "6"] and len(closed(r)) == 1
    assert sum(D(f["qty"]) for f in fills(r)) == 10


def test_same_approval_again_while_the_order_is_still_open_sends_nothing(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        rec = r.approve(price="100", seq=1)
        await r.settle()
        assert r.state()["orders"][cid_of(rec)]["state"] == "open"
        await r.execution.handle(rec)
        await r.stop()

    run(main())
    assert r.sim.submit_calls == 1 and len(r.log("OrderIntent")) == 1


def test_unreachable_broker_during_reconciliation_is_a_divergence(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "50"), ("6", "50")])

    async def first():
        await r.start()
        r.approve(seq=1)
        await r.settle()
        await r.stop()

    run(first())
    r.restart()
    r.sim.inject_lookup("error")   # a consulta da ordem aberta falha na partida

    async def second():
        await r.start()
        await r.stop()

    run(second())
    kinds = [d["kind"] for d in r.log("Reconciled")[-1]["payload"]["divergences"]]
    assert "corretora_indisponivel" in kinds and "reconciliacao_divergente" in r.rules()
    assert r.execution.reconciled is False


def test_unreachable_broker_when_comparing_positions_is_a_divergence(tmp_path, clock, monkeypatch):
    r = Rig(tmp_path, clock)

    async def broken():
        raise BrokerError("sem rede")

    async def main():
        await r.start(reconcile=False)
        monkeypatch.setattr(r.sim, "positions", broken)
        divs = await r.execution.reconcile()
        await r.stop()
        return divs

    divs = run(main())
    assert [d["kind"] for d in divs] == ["corretora_indisponivel"]
    assert "reconciliacao_divergente" in r.rules() and r.execution.reconciled is False


def test_order_that_shows_up_late_after_being_closed_as_not_sent_is_a_divergence(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.inject("timeout")

    async def main():
        await r.start()
        rec = r.approve(seq=1)
        await r.settle()
        await r.execution.clear_halt(r.risk_agent, "ruan", "conferi")   # não achou: fechou como not_sent
        await r.settle()
        # a ordem chega à corretora depois da consulta (a janela residual declarada)
        r.sim.plan_next([("4", "50"), ("6", "50")])
        r.sim.create_now(OrderRequest(cid_of(rec), "AAA", "buy", D("10"), D("50")))
        divs = await r.execution.reconcile()
        await r.stop()
        return divs

    divs = run(main())
    assert sorted(d["kind"] for d in divs) == ["ordem_reaberta", "posicao"]
    assert "reconciliacao_divergente" in r.rules()
