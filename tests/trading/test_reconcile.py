from decimal import Decimal as D

import pytest

from sentinela.trading.execution import ReconciliationFailed
from sentinela.trading.risk import ReconciliationRequired
from tests.trading.exec_env import Rig, run
from tests.trading.test_execution import cid_of, closed, fills


def divs(r):
    return [x["payload"] for x in r.log("Reconciled")][-1]["divergences"]


def kinds(r):
    return sorted(d["kind"] for d in divs(r))


async def one_buy(r, ticker="AAA", price="50", qtd="10", seq=1):
    rec = r.approve(ticker=ticker, qtd=qtd, price=price, seq=seq)
    await r.settle()
    return rec


# --- critério 8: reconciliação na partida ---------------------------------------------------

def test_clean_reconciliation_after_restart_with_an_open_order_continues_it(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def first():
        await r.start()
        await one_buy(r)   # AAA 10 completa
        r.sim.plan_next([("4", "50"), ("6", "50")])
        await one_buy(r, ticker="BBB", seq=2)   # BBB 4 de 10, aberta
        await r.stop()

    run(first())
    r.restart()

    async def second():
        await r.start()
        assert r.execution.reconciled is True
        r.sim.advance()
        await r.settle()
        await r.stop()

    run(second())
    rec = r.log("Reconciled")[-1]["payload"]
    assert rec["divergences"] == [] and r.rules() == []
    assert D(r.portfolio.view()["positions"]["BBB"]["qty"]) == 10   # a ordem aberta seguiu
    assert len([c for c in closed(r) if c["status"] == "filled"]) == 2


def test_position_on_the_broker_that_the_portfolio_does_not_have_halts_and_blocks_sends(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def first():
        await r.start()
        await one_buy(r)
        await r.stop()

    run(first())
    r.sim.set_positions({"AAA": D("12")})
    r.restart()

    async def second():
        await r.start()
        r.forge(ticker="BBB", approval_id="depois", corr="depois")   # o Risk já vetaria: força a aprovação
        await r.settle()
        await r.stop()

    run(second())
    d = divs(r)
    assert [(x["kind"], x["ticker"], x["broker"], x["portfolio"]) for x in d] == [
        ("posicao", "AAA", "12", "10")]
    assert "reconciliacao_divergente" in r.rules() and r.sim.submit_calls == 1
    assert closed(r)[-1]["reason"] == "sem_reconciliacao"   # a partida divergente não liberou o envio


def _foreign_position(r):
    r.sim.set_positions({"AAA": D("10"), "BBB": D("3")})


def _foreign_order(r):
    r.sim.add_foreign_order("manual-1", "BBB", "buy", D("5"))


@pytest.mark.parametrize("tamper, kind", [
    (_foreign_position, "posicao"),
    (_foreign_order, "ordem_desconhecida"),
])
def test_divergences_that_halt(tmp_path, clock, tamper, kind):
    r = Rig(tmp_path, clock)

    async def first():
        await r.start()
        await one_buy(r)
        await r.stop()

    run(first())
    tamper(r)
    r.restart()

    async def second():
        await r.start()
        await r.stop()

    run(second())
    assert kind in kinds(r) and "reconciliacao_divergente" in r.rules()


def test_open_order_that_the_broker_forgot_is_a_divergence(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def first():
        await r.start()
        r.sim.plan_next([("4", "50"), ("6", "50")])
        await one_buy(r)
        await r.stop()

    run(first())
    r.sim.forget(next(iter(r.state()["orders"])))
    r.sim.set_positions({"AAA": D("4")})   # a posição continua batendo; só a ordem sumiu
    r.restart()

    async def second():
        await r.start()
        await r.stop()

    run(second())
    assert kinds(r) == ["ordem_sumiu"] and "reconciliacao_divergente" in r.rules()


def test_fill_in_the_log_that_the_portfolio_never_applied_is_a_divergence(tmp_path, clock):
    r = Rig(tmp_path, clock)
    saved = {}

    async def first():
        await r.start()
        saved["portfolio"] = (tmp_path / "portfolio.json").read_bytes()   # carteira antes do fill
        await one_buy(r)
        await r.stop()

    run(first())
    (tmp_path / "portfolio.json").write_bytes(saved["portfolio"])   # a queda perdeu o Fill na carteira
    r.restart()

    async def second():
        await r.start()
        await r.stop()

    run(second())
    d = divs(r)
    assert [(x["kind"], x["broker"], x["portfolio"]) for x in d] == [("posicao", "10", "0")]


def test_order_filled_while_the_system_was_down_is_caught_up_without_divergence(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "50"), ("6", "50")])

    async def first():
        await r.start()
        await one_buy(r)   # 4 de 10 publicados
        await r.stop()

    run(first())
    r.sim.advance()   # com o sistema fora, a corretora completou: corretora 10, log 4, carteira 4
    r.restart()

    async def second():
        await r.start()
        await r.settle()
        await r.stop()

    run(second())
    assert divs(r) == [] and r.rules() == []
    assert [f["qty"] for f in fills(r)] == ["4", "6"] and closed(r)[0]["filled_qty"] == "10"
    assert D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 10


@pytest.mark.parametrize("placed", [False, True])
def test_intent_without_a_confirmed_send_is_resolved_by_the_broker_answer(tmp_path, clock, placed):
    r = Rig(tmp_path, clock)

    def hook(req):
        if placed:
            r.sim.create_now(req)   # a ordem chegou à corretora
        raise RuntimeError("queda no meio do envio")

    r.sim.before_submit = hook

    async def first():
        await r.start()
        rec = r.approve(seq=1)
        await r.settle()
        await r.stop()
        return rec

    rec = run(first())
    assert r.state()["orders"][cid_of(rec)]["state"] == "intent"
    assert "agente_execution_caiu" in r.rules()
    r.sim.before_submit = None
    r.restart()

    async def second():
        await r.start()
        await r.execution.clear_halt(r.risk_agent, "ruan", "conferi")
        await r.settle()
        await r.stop()

    run(second())
    assert r.sim.submit_calls == 1   # nunca reenvia
    st = r.state()["orders"][cid_of(rec)]["state"]
    if placed:   # a corretora tem: adota, publica o fill, sem divergência
        assert st == "closed" and D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 10
        assert r.log("OrderSubmitted")[0]["payload"]["outcome"] == "adopted_on_reconcile"
    else:        # a corretora não tem: nunca saiu
        assert st == "not_sent" and closed(r)[0]["status"] == "not_sent"
        assert r.risk_state()["pending"] == {}


def test_approved_in_the_log_without_any_state_is_closed_as_not_sent_after_restart(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def first():
        await r.start()
        r.approve(seq=1)   # entra na fila do Execution, que "cai" antes de processar
        await r.stop()

    run(first())
    r.restart()

    async def second():
        await r.start()
        await r.settle()
        await r.stop()

    run(second())
    c = closed(r)[0]
    assert c["status"] == "not_sent" and c["reason"] == "reinicio" and r.sim.submit_calls == 0
    assert r.risk_state()["pending"] == {} and r.risk_state()["trades_today"] == 0


def test_approval_waiting_in_the_queue_during_a_clear_is_closed_and_the_late_event_ignored(
        tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        r.halts.raise_("fila_cheia", "integrity")
        r.forge(approval_id="na-fila", corr="na-fila")
        await r.execution.clear_halt(r.risk_agent, "ruan", "conferi")   # fecha a que está na fila
        await r.settle()   # o evento atrasado chega depois: id já tratado
        await r.stop()

    run(main())
    assert r.sim.submit_calls == 0
    assert len(closed(r, "na-fila")) == 1


def test_missing_or_corrupt_execution_state_halts(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def first():
        await r.start()
        await one_buy(r)
        await r.stop()

    run(first())
    (tmp_path / "execution_state.json").unlink()
    r.restart()
    assert "execution_estado_ausente" in r.rules()
    r.halts.clear("t", "t")
    (tmp_path / "execution_state.json").write_text("{lixo", encoding="utf-8")
    r.restart()
    assert "execution_estado_corrompido" in r.rules()


def test_invalid_order_that_stays_inconsistent_keeps_blocking_the_clear(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("4", "100"), ("6", "101")])

    async def main():
        await r.start()
        rec = r.approve(price="100", seq=1)
        await r.settle()
        r.sim.override(cid_of(rec), filled_qty=D("2"))
        await r.settle()
        assert "broker_invalido" in r.rules()
        with pytest.raises(ReconciliationFailed) as e:
            await r.execution.clear_halt(r.risk_agent, "ruan", "olhei")
        assert [d["kind"] for d in e.value.divergences] == ["ordem_invalida"]
        assert r.state()["orders"][cid_of(rec)]["state"] == "invalid"
        r.sim.override(cid_of(rec))   # a corretora volta a responder direito
        await r.execution.clear_halt(r.risk_agent, "ruan", "agora sim")
        r.sim.advance()
        await r.settle()
        await r.stop()

    run(main())
    assert r.rules() == [] and closed(r)[0]["filled_qty"] == "10"


# --- critério 9: clear_halt de integridade só depois de reconciliar -------------------------

def test_clear_is_refused_while_the_divergence_persists_and_granted_once_fixed(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def first():
        await r.start()
        await one_buy(r)
        await r.stop()

    run(first())
    r.sim.set_positions({"AAA": D("12")})
    r.restart()

    async def second():
        await r.start()
        assert "reconciliacao_divergente" in r.rules()
        with pytest.raises(ReconciliationFailed):
            await r.execution.clear_halt(r.risk_agent, "ruan", "tentei")
        assert "reconciliacao_divergente" in r.rules()
        assert len(r.log("HaltClearRefused")) == 1 and r.log("HaltCleared") == []
        r.sim.set_positions({"AAA": D("10")})   # a divergência foi resolvida na corretora
        cleared = await r.execution.clear_halt(r.risk_agent, "ruan", "corrigi")
        await r.settle()
        assert "reconciliacao_divergente" in [c.rule for c in cleared]
        r.approve(ticker="BBB", seq=3)
        await r.settle()
        await r.stop()

    run(second())
    assert r.rules() == [] and r.sim.submit_calls == 2
    assert r.log("HaltCleared")[0]["payload"]["who"] == "ruan"


def test_risk_agent_clear_of_an_integrity_halt_needs_the_reconciliation_flag(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        r.halts.raise_("fila_cheia", "integrity")
        with pytest.raises(ReconciliationRequired):
            r.risk_agent.clear_halt("ruan", "olhei")
        assert r.rules() == ["fila_cheia"] and r.log("HaltCleared") == []
        await r.stop()

    run(main())


def test_clearing_only_a_limit_halt_does_not_touch_the_broker(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def main():
        await r.start()
        r.bus.kill("ruan", "teste")
        reads = r.sim.read_calls
        await r.execution.clear_halt(r.risk_agent, "ruan", "ok")
        assert r.sim.read_calls == reads
        await r.settle()
        r.approve(seq=1)
        await r.settle()
        await r.stop()

    run(main())
    assert r.rules() == [] and r.sim.submit_calls == 1


def test_finished_order_missing_from_a_restored_state_file_is_not_reinvented(tmp_path, clock):
    r = Rig(tmp_path, clock)
    saved = {}

    async def first():
        await r.start()
        saved["state"] = (tmp_path / "execution_state.json").read_bytes()   # arquivo ainda sem a ordem
        await one_buy(r)
        await r.stop()

    run(first())
    (tmp_path / "execution_state.json").write_bytes(saved["state"])   # backup antigo restaurado
    r.restart()

    async def second():
        await r.start()
        await r.stop()

    run(second())
    assert r.state()["orders"] == {}   # a ordem já tinha fechado no log: não vira "not_sent" inventada
    assert len(closed(r)) == 1 and divs(r) == []


def test_fills_already_in_the_log_are_summed_after_a_restart_not_published_again(tmp_path, clock):
    r = Rig(tmp_path, clock)
    r.sim.plan_next([("3", "50"), ("3", "50"), ("4", "50")])

    async def first():
        await r.start()
        await one_buy(r)
        r.sim.advance()
        await r.settle()   # 3 + 3 publicados
        await r.stop()

    run(first())
    r.restart()

    async def second():
        await r.start()
        await r.settle()
        assert [f["qty"] for f in fills(r)] == ["3", "3"]   # nada repetido
        r.sim.advance()
        await r.settle()
        await r.stop()

    run(second())
    assert [f["qty"] for f in fills(r)] == ["3", "3", "4"] and divs(r) == []
    assert D(r.portfolio.view()["positions"]["AAA"]["qty"]) == 10


def test_corrupt_execution_state_file_is_left_untouched_for_inspection(tmp_path, clock):
    r = Rig(tmp_path, clock)
    (tmp_path / "execution_state.json").write_bytes(b"{lixo")
    r.restart()
    assert "execution_estado_corrompido" in r.rules()

    async def main():
        await r.start()
        r.forge(approval_id="depois", corr="depois")   # tenta gravar intenção e fechamento
        await r.settle()
        await r.stop()

    run(main())
    assert (tmp_path / "execution_state.json").read_bytes() == b"{lixo"   # nada por cima
    assert r.sim.submit_calls == 0   # o halt de integridade segura o envio
    assert closed(r, "depois")[0]["status"] == "not_sent"   # o Risk ainda solta a reserva pelo log


def test_missing_execution_state_is_rewritten_because_there_is_nothing_to_preserve(tmp_path, clock):
    r = Rig(tmp_path, clock)

    async def first():
        await r.start()
        await one_buy(r)
        await r.stop()

    run(first())
    (tmp_path / "execution_state.json").unlink()
    r.restart()
    assert "execution_estado_ausente" in r.rules()
    r.halts.clear("ruan", "conferi")

    async def second():
        await r.start()
        await r.stop()

    run(second())
    assert (tmp_path / "execution_state.json").exists()   # estado ausente: grava de novo, sem o que se perdeu
