import asyncio
import json
from decimal import Decimal as D

import pytest

from sentinela.trading.audit import read_log
from sentinela.trading.risk import ReconciliationRequired
from tests.trading.shared_loop import run
from tests.trading.test_agents import System
from tests.trading.test_risk import Env, buy, cfg, ok, pos, sell, vetoed, view


def state_of(tmp_path):
    return json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))


# --- critério 7: venda parcial aberta desconta só o que falta -------------------------------

@pytest.mark.parametrize("position, applied", [(10, 19), (7, 20)])   # foto antiga e foto nova
def test_open_partial_sell_leaves_four_available_before_and_after_the_view_applies_the_fill(
        tmp_path, clock, position, applied):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}, applied_seq=10))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    assert e.risk.on_fill(a["approval_id"], D("3"), seq=20) is True
    e.set_view(view(positions={"AAA": pos(position)}, applied_seq=applied))
    v = vetoed(e.decide(sell(qtd="5", seq=2)), "sell_excede")
    ok(e.decide(sell(qtd="4", seq=3)))   # controle positivo: sobram 4
    assert D(v["values"]["vendas_abertas"]) in (D("6"), D("3"))   # nunca a ordem inteira depois do fill aplicado


def test_fill_not_yet_in_the_view_is_not_discounted_early(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    e.risk.on_fill(a["approval_id"], D("3"), seq=20)
    e.set_view(view(positions={"AAA": pos(10)}, applied_seq=19))
    vetoed(e.decide(sell(qtd="7", seq=2)), "sell_excede")   # descontar cedo daria 10 - 3 = 7 e viraria short


def test_applied_fill_is_discounted_once_the_view_reaches_its_seq(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    e.risk.on_fill(a["approval_id"], D("3"), seq=20)
    e.set_view(view(positions={"AAA": pos(7)}, applied_seq=20))
    ok(e.decide(sell(qtd="4", seq=2)))   # sem o desconto: 7 - 6 = 1 e a venda de 4 seria vetada


def test_fills_beyond_the_order_quantity_never_make_the_open_sell_negative(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    e.risk.on_fill(a["approval_id"], D("4"), seq=20)
    e.risk.on_fill(a["approval_id"], D("4"), seq=21)   # 8 > 6: não existe, mas não pode abrir folga
    e.set_view(view(positions={"AAA": pos(2)}, applied_seq=21))
    vetoed(e.decide(sell(qtd="3", seq=2)), "sell_excede")
    ok(e.decide(sell(qtd="2", seq=3)))


def test_partial_sell_fills_survive_restart(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    e.risk.on_fill(a["approval_id"], D("3"), seq=20)
    e.restart(v=view(positions={"AAA": pos(7)}, applied_seq=20))
    vetoed(e.decide(sell(qtd="5", seq=2)), "sell_excede")
    ok(e.decide(sell(qtd="4", seq=3)))


def test_buy_fill_does_not_touch_the_reservation(tmp_path, clock):
    e = Env(tmp_path, clock)
    a = ok(e.decide(buy(ticker="AAA", seq=1)))
    assert e.risk.on_fill(a["approval_id"], D("4"), seq=20) is True
    st = state_of(tmp_path)
    assert st["pending"][a["approval_id"]]["reserve"] == "500" and st["trades_today"] == 1
    e.set_view(view(positions={"AAA": pos(4, mark="50")}, applied_seq=20))
    vetoed(e.decide(buy(ticker="AAA", seq=2)), "posicao_existente")


def test_old_state_file_without_fills_still_loads(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    ok(e.decide(sell(qtd="6", seq=1)))
    st = state_of(tmp_path)
    for r in st["pending"].values():
        r.pop("fills", None)
    (tmp_path / "risk_state.json").write_text(json.dumps(st), encoding="utf-8")
    e.restart(v=view(positions={"AAA": pos(10)}))
    vetoed(e.decide(sell(qtd="5", seq=2)), "sell_excede")


# --- critério 10: id desconhecido -----------------------------------------------------------

def test_unknown_ids_are_reported_by_the_risk_methods(tmp_path, clock):
    e = Env(tmp_path, clock)
    a = ok(e.decide(buy(seq=1)))
    assert e.risk.on_order_closed("fantasma", D("0")) is False
    assert e.risk.on_fill("fantasma", D("1"), seq=5) is False
    assert state_of(tmp_path)["trades_today"] == 1   # nada mudou
    assert e.risk.on_order_closed(a["approval_id"], D("0")) is True   # controle positivo
    assert e.risk.on_order_closed(a["approval_id"], D("0")) is False   # o 2º fechamento é desconhecido


def test_unknown_order_closed_and_fill_are_written_to_the_log(tmp_path, clock):
    s = System(tmp_path, clock)

    async def main():
        await s.bus.start()
        await s.portfolio_agent.announce()
        s.signal("sig-1")
        await s.bus.drain()
        approval_id = s.seen[0][1]["approval_id"]
        s.execution.publish("OrderClosed", {"approval_id": approval_id, "status": "rejected",
                                            "filled_qty": "0"})
        await s.bus.drain()   # conhecido: nada de aviso
        s.execution.publish("OrderClosed", {"approval_id": approval_id, "status": "rejected",
                                            "filled_qty": "0"})   # 2º fechamento
        s.execution.publish("OrderClosed", {"approval_id": "fantasma", "status": "filled",
                                            "filled_qty": "7"})
        s.execution.publish("Fill", {"approval_id": "fantasma2", "ticker": "AAA", "side": "sell",
                                     "qty": "1", "price": "10"})
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    s.audit.close()
    ign = [r for r in read_log(tmp_path / "audit.jsonl") if r["type"] == "OrderEventIgnored"]
    assert [(r["agent"], r["payload"]["event"], r["payload"]["approval_id"]) for r in ign] == [
        ("risk", "OrderClosed", s_first_id(tmp_path)), ("risk", "OrderClosed", "fantasma"),
        ("risk", "Fill", "fantasma2")]
    assert ign[1]["payload"]["qty"] == "7" and ign[2]["payload"]["qty"] == "1"
    assert all(r["causation_id"] is not None for r in ign)


def s_first_id(tmp_path):
    return next(r["payload"]["approval_id"] for r in read_log(tmp_path / "audit.jsonl")
                if r["type"] == "OrderApproved")


# --- critério 10: pendência em disco sem evento no log --------------------------------------

def test_release_orphans_returns_only_pending_without_a_logged_approval(tmp_path, clock):
    e = Env(tmp_path, clock)
    a = ok(e.decide(buy(ticker="AAA", seq=1)))
    b = ok(e.decide(buy(ticker="BBB", seq=2)))
    assert state_of(tmp_path)["trades_today"] == 2
    assert e.risk.release_orphans({b["approval_id"]}) == [a["approval_id"]]
    st = state_of(tmp_path)
    assert list(st["pending"]) == [b["approval_id"]] and st["trades_today"] == 1
    assert e.risk.release_orphans({b["approval_id"]}) == []   # nada mais a soltar
    ok(e.decide(buy(ticker="AAA", seq=3)))   # a reserva de AAA foi devolvida


def test_agent_releases_the_orphan_and_logs_it_but_keeps_the_logged_one(tmp_path, clock):
    s = System(tmp_path, clock)
    sig = {"seq": 99, "correlation_id": "perdida",
           "payload": {"side": "buy", "ticker": "BBB", "target_weight": "0.05", "ref_price": "50",
                       "bar_id": "b9"}}

    async def main():
        await s.bus.start()
        await s.portfolio_agent.announce()
        s.signal("sig-ok")
        await s.bus.drain()
        kind, payload = s.risk.decide(sig)   # gravou a reserva e "caiu" antes de publicar
        assert kind == "approved"
        released = s.risk_agent.release_orphans()
        await s.bus.drain()
        await s.bus.stop()
        return payload["approval_id"], released

    lost_id, released = run(main())
    s.audit.close()
    assert released == [lost_id]
    log = read_log(tmp_path / "audit.jsonl")
    orph = [r for r in log if r["type"] == "PendingOrphaned"]
    assert len(orph) == 1 and orph[0]["payload"]["approval_id"] == lost_id
    assert orph[0]["agent"] == "risk"
    st = state_of(tmp_path)
    assert list(st["pending"]) == [s_first_id(tmp_path)] and st["trades_today"] == 1


# --- critério 9: clear_halt de integridade exige reconciliação ------------------------------

def test_risk_clear_halt_of_integrity_requires_reconciliation(tmp_path, clock):
    e = Env(tmp_path, clock)
    e.halts.raise_("fila_cheia", "integrity")
    with pytest.raises(ReconciliationRequired):
        e.risk.clear_halt("ruan", "olhei")
    assert [r.rule for r in e.halts.active()] == ["fila_cheia"]   # nada foi limpo
    cleared, _ = e.risk.clear_halt("ruan", "olhei", reconciled=True)
    assert [r.rule for r in cleared] == ["fila_cheia"]


def test_risk_clear_halt_of_limit_only_needs_no_reconciliation(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(equity="8000", peak="10000"))
    vetoed(e.decide(buy()), "drawdown")
    cleared, _ = e.risk.clear_halt("ruan", "conferi")
    assert [r.rule for r in cleared] == ["drawdown"]


# --- ajustes do crítico: reinício, dois fills, órfãs de venda e de outro dia, 2º fechamento --

def test_after_restart_with_a_fresh_view_at_applied_seq_zero_the_discount_is_conservative(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    e.risk.on_fill(a["approval_id"], D("3"), seq=20)
    e.restart(v=view(positions={"AAA": pos(7)}, applied_seq=0))   # a carteira já tem o fill, a foto não diz
    vetoed(e.decide(sell(qtd="2", seq=2)), "sell_excede")   # 7 - 6 = 1, nunca 4
    ok(e.decide(sell(qtd="1", seq=3)))


def test_two_fills_with_only_the_first_applied_discounts_only_the_first(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    e.risk.on_fill(a["approval_id"], D("3"), seq=20)
    e.risk.on_fill(a["approval_id"], D("2"), seq=21)
    e.set_view(view(positions={"AAA": pos(7)}, applied_seq=20))
    vetoed(e.decide(sell(qtd="5", seq=2)), "sell_excede")   # 7 - (6 - 3) = 4
    ok(e.decide(sell(qtd="4", seq=3)))


def test_orphan_sell_does_not_touch_trades_today_and_previous_day_buy_does_not_decrement(tmp_path, clock):
    from datetime import date
    e = Env(tmp_path, clock, v=view(positions={"MSFT": pos(10)}))
    b = ok(e.decide(buy(ticker="AAA", seq=1)))
    s = ok(e.decide(sell(ticker="MSFT", qtd="4", seq=2)))
    assert state_of(tmp_path)["trades_today"] == 1
    assert e.risk.release_orphans({b["approval_id"]}) == [s["approval_id"]]
    assert state_of(tmp_path)["trades_today"] == 1   # venda órfã: o contador não muda
    clock.set_date(date(2026, 7, 21))
    e.set_view(view(positions={"MSFT": pos(10)}))
    c = ok(e.decide(buy(ticker="BBB", seq=3)))   # compra de hoje: trades_today = 1
    assert e.risk.release_orphans({c["approval_id"]}) == [b["approval_id"]]   # a de ontem, órfã
    assert state_of(tmp_path)["trades_today"] == 1   # a de ontem não tira o trade de hoje


def test_second_order_closed_while_the_reservation_is_still_held_changes_nothing(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    assert e.risk.on_order_closed(a["approval_id"], D("6"), seq=50) is True   # segura até a carteira aplicar
    before = state_of(tmp_path)
    assert e.risk.on_order_closed(a["approval_id"], D("0"), seq=51) is False   # repetido: ignorado
    assert state_of(tmp_path) == before   # closed_seq e reserva intactos
    vetoed(e.decide(sell(qtd="7", seq=2)), "sell_excede")   # a venda continua pesando
