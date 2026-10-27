import asyncio
import json
from datetime import date
from decimal import Decimal as D

import pytest

from sentinela.trading import store
from sentinela.trading.audit import AuditLog
from sentinela.trading.bus import EventBus
from sentinela.trading.halts import HaltStore
from sentinela.trading.portfolio import (
    InvalidFill, MarkMissing, Portfolio, PortfolioAgent, PortfolioCorrupt, PortfolioStateCorrupt,
    PortfolioStateMissing,
)
from sentinela.trading.store import StateWriteError


def new(tmp_path, clock, cash="10000", first_run=True):
    return Portfolio(tmp_path / "portfolio.json", clock, initial_cash=D(cash), first_run=first_run)


def test_cash_only_equity(tmp_path, clock):
    p = new(tmp_path, clock)
    v = p.view()
    assert D(v["equity"]) == 10000 and D(v["cash"]) == 10000
    assert v["positions"] == {} and v["consecutive_losses"] == 0
    assert D(v["peak_equity"]) == 10000 and D(v["daily_pnl"]) == 0 and D(v["weekly_pnl"]) == 0


def test_partial_fills_average_and_realized(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("4"), D("100"))
    p.apply_fill("AAA", "buy", D("6"), D("101"))
    pos = p.view()["positions"]["AAA"]
    assert D(pos["qty"]) == 10 and D(pos["avg"]) == D("100.60")
    realized = p.apply_fill("AAA", "sell", D("5"), D("110"))
    assert realized == D("47.00")
    pos = p.view()["positions"]["AAA"]
    assert D(pos["qty"]) == 5 and D(pos["avg"]) == D("100.60")  # venda não muda o médio


def test_equity_is_cash_plus_quantity_times_last_mark(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))  # caixa 9000
    p.set_mark("AAA", D("110"))
    v = p.view()
    assert D(v["cash"]) == 9000 and D(v["equity"]) == 10100
    assert D(v["positions"]["AAA"]["mark"]) == 110


def test_peak_updates_with_equity_and_never_falls(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    p.set_mark("AAA", D("120"))
    assert D(p.view()["peak_equity"]) == 10200
    p.set_mark("AAA", D("90"))
    v = p.view()
    assert D(v["equity"]) == 9900 and D(v["peak_equity"]) == 10200


def test_daily_gap_uses_previous_close_not_first_snapshot_of_day(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("100"), D("100"))  # caixa 0, equity 10.000
    clock.set_date(date(2026, 7, 21))
    p.set_mark("AAA", D("95"))  # abre com gap de -5%
    v = p.view()
    assert D(v["equity"]) == 9500
    assert D(v["daily_pnl"]) == D("-0.05")


def test_weekly_base_is_close_of_previous_week(tmp_path, clock):
    clock.set_date(date(2026, 7, 17))  # sexta
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("100"), D("100"))  # fechamento da sexta: 10.000
    clock.set_date(date(2026, 7, 20))  # segunda abre com gap: a base semanal não pode ser esta foto
    p.set_mark("AAA", D("99"))
    clock.set_date(date(2026, 7, 21))  # terça fecha em 9.600
    p.set_mark("AAA", D("96"))
    clock.set_date(date(2026, 7, 22))  # quarta
    p.set_mark("AAA", D("93.9"))
    v = p.view()
    assert D(v["equity"]) == D("9390")
    assert round(float(D(v["daily_pnl"])), 4) == -0.0219   # (9390-9600)/9600
    assert D(v["weekly_pnl"]) == D("-0.061")               # (9390-10000)/10000


def test_consecutive_losses_by_closed_cycle(tmp_path, clock):
    p = new(tmp_path, clock)

    def cycle(sell_price):
        p.apply_fill("AAA", "buy", D("10"), D("100"))
        p.apply_fill("AAA", "sell", D("10"), D(sell_price))
        return p.view()["consecutive_losses"]

    assert cycle("90") == 1   # realizado negativo
    assert cycle("95") == 2   # outro negativo
    assert cycle("100") == 0  # realizado zero zera a sequência
    assert cycle("90") == 1
    assert cycle("110") == 0  # positivo zera


def test_partial_sell_does_not_close_cycle(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    p.apply_fill("AAA", "sell", D("5"), D("90"))
    assert p.view()["consecutive_losses"] == 0  # ciclo ainda aberto
    p.apply_fill("AAA", "sell", D("5"), D("90"))
    assert p.view()["consecutive_losses"] == 1


def test_cycle_realized_accumulates_over_partial_sells(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    p.apply_fill("AAA", "sell", D("5"), D("120"))  # +100
    p.apply_fill("AAA", "sell", D("5"), D("95"))   # -25: o ciclo fecha em +75, não é perda
    assert p.view()["consecutive_losses"] == 0


def test_fill_that_would_make_quantity_negative_is_rejected_and_state_untouched(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    before = p.view()
    with pytest.raises(PortfolioCorrupt):
        p.apply_fill("AAA", "sell", D("11"), D("100"))
    assert p.view() == before
    with pytest.raises(PortfolioCorrupt):  # vender o que nunca se teve
        p.apply_fill("ZZZ", "sell", D("1"), D("10"))


def test_state_survives_restart_including_marks(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    p.set_mark("AAA", D("120"))
    again = Portfolio(tmp_path / "portfolio.json", clock)  # reinício com posição aberta
    v = again.view()  # não levanta MarkMissing
    assert D(v["equity"]) == 10200 and D(v["peak_equity"]) == 10200
    assert D(v["positions"]["AAA"]["mark"]) == 120


def test_position_that_never_had_a_mark_is_missing_mark(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    raw = json.loads((tmp_path / "portfolio.json").read_text(encoding="utf-8"))
    raw["marks"] = {}
    (tmp_path / "portfolio.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MarkMissing):
        Portfolio(tmp_path / "portfolio.json", clock).view()


def test_missing_and_corrupt_state_are_errors_not_fresh_start(tmp_path, clock):
    with pytest.raises(PortfolioStateMissing):
        Portfolio(tmp_path / "portfolio.json", clock)
    (tmp_path / "portfolio.json").write_text("{lixo", encoding="utf-8")
    with pytest.raises(PortfolioStateCorrupt):
        Portfolio(tmp_path / "portfolio.json", clock)


def test_view_is_a_copy(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    v = p.view()
    v["positions"]["AAA"]["qty"] = "999"
    v["cash"] = "0"
    again = p.view()
    assert D(again["positions"]["AAA"]["qty"]) == 10 and D(again["cash"]) == 9000


def test_rebase_resets_peak_and_losses(tmp_path, clock):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    p.set_mark("AAA", D("120"))
    p.set_mark("AAA", D("80"))
    p.apply_fill("AAA", "sell", D("10"), D("80"))
    assert p.view()["consecutive_losses"] == 1
    p.rebase()
    v = p.view()
    assert D(v["peak_equity"]) == D(v["equity"]) and v["consecutive_losses"] == 0


@pytest.mark.parametrize("side, qty, price", [
    ("BUY", "1", "100"), ("short", "1", "100"), (None, "1", "100"), ("", "1", "100"),
    ("buy", "0", "100"), ("buy", "-1", "100"), ("buy", "nan", "100"), ("buy", "inf", "100"),
    ("buy", "1", "0"), ("buy", "1", "-5"), ("buy", "1", "nan"), ("buy", "1", "inf"),
    ("sell", "0", "100"), ("sell", "-1", "100"), ("sell", "nan", "100"), ("sell", "1", "nan"),
])
def test_invalid_fill_is_rejected_and_nothing_changes(tmp_path, clock, side, qty, price):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    before, on_disk = p.view(), (tmp_path / "portfolio.json").read_bytes()
    with pytest.raises(InvalidFill):
        p.apply_fill("AAA", side, D(qty), D(price))
    assert p.view() == before
    assert (tmp_path / "portfolio.json").read_bytes() == on_disk
    p.apply_fill("AAA", "sell", D("1"), D("100"))  # controle positivo: fill válido segue passando


@pytest.mark.parametrize("price", ["0", "-1", "nan", "inf", "-inf"])
def test_invalid_mark_is_rejected_and_nothing_changes(tmp_path, clock, price):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    before = p.view()
    with pytest.raises(InvalidFill):
        p.set_mark("AAA", D(price))
    assert p.view() == before
    p.set_mark("AAA", D("101"))  # controle positivo


@pytest.mark.parametrize("peak, losses", [(True, False), (False, True), (False, False), (True, True)])
def test_rebase_only_resets_what_was_asked(tmp_path, clock, peak, losses):
    p = new(tmp_path, clock)
    p.apply_fill("AAA", "buy", D("10"), D("100"))
    p.set_mark("AAA", D("120"))
    p.set_mark("AAA", D("100"))
    p.apply_fill("AAA", "sell", D("10"), D("90"))   # perda de ciclo: losses = 1
    before = p.view()
    assert D(before["peak_equity"]) == 10200 and before["consecutive_losses"] == 1
    p.rebase(peak=peak, losses=losses, reset_id="r1")
    v = p.view()
    assert (D(v["peak_equity"]) == D(v["equity"])) is peak
    assert (v["consecutive_losses"] == 0) is losses
    assert v["reset_id"] == "r1"


def test_reset_id_survives_restart(tmp_path, clock):
    p = new(tmp_path, clock)
    p.rebase(peak=False, losses=False, reset_id="r9")
    assert Portfolio(tmp_path / "portfolio.json", clock).view()["reset_id"] == "r9"


def test_write_failure_surfaces(tmp_path, clock, monkeypatch):
    p = new(tmp_path, clock)

    def boom(path, obj):
        raise StateWriteError("disco cheio")

    monkeypatch.setattr(store, "atomic_write_json", boom)
    with pytest.raises(StateWriteError):
        p.apply_fill("AAA", "buy", D("1"), D("100"))


# --- agente ---------------------------------------------------------------------------------

def agent_env(tmp_path, clock, **kw):
    audit = AuditLog(tmp_path / "audit.jsonl", clock)
    halts = HaltStore(tmp_path / "halts.json", clock, audit=audit, first_run=True)
    bus = EventBus(audit, halts, clock, marker_path=tmp_path / "LOOP_TRAVADO")
    portfolio = new(tmp_path, clock, **kw)
    agent = PortfolioAgent(portfolio, bus)
    views = []

    async def collect(ev):
        views.append(ev["payload"])

    risk_pub = bus.register("risk", collect, subscribes=["PortfolioUpdated"])
    return bus, halts, portfolio, agent, views, risk_pub


def test_fill_and_mark_events_publish_updated_view(tmp_path, clock):
    bus, halts, portfolio, agent, views, _ = agent_env(tmp_path, clock)
    execution = bus.register("execution")
    data = bus.register("data")

    async def main():
        await bus.start()
        await agent.announce()
        execution.publish("Fill", {"approval_id": "a1", "ticker": "AAA", "side": "buy",
                                   "qty": "10", "price": "100"})
        data.publish("MarkPrice", {"ticker": "AAA", "price": "110"})
        await bus.drain()
        await bus.stop()

    asyncio.run(main())
    assert [D(v["equity"]) for v in views] == [10000, 10000, 10100]
    seqs = [v["applied_seq"] for v in views]
    assert seqs[0] == 0 and seqs == sorted(set(seqs))  # a foto diz até que evento já foi aplicada
    assert halts.active() == []


def test_negative_fill_event_is_integrity_halt_and_no_view(tmp_path, clock):
    bus, halts, portfolio, agent, views, _ = agent_env(tmp_path, clock)
    execution = bus.register("execution")

    async def main():
        await bus.start()
        execution.publish("Fill", {"approval_id": "a1", "ticker": "AAA", "side": "sell",
                                   "qty": "1", "price": "100"})
        await bus.drain()
        await bus.stop()

    asyncio.run(main())
    assert [r.rule for r in halts.active()] == ["fill_negativo"]
    assert views == []


def test_missing_mark_event_is_integrity_halt(tmp_path, clock):
    bus, halts, portfolio, agent, views, _ = agent_env(tmp_path, clock)
    portfolio.apply_fill("AAA", "buy", D("10"), D("100"))
    raw = json.loads((tmp_path / "portfolio.json").read_text(encoding="utf-8"))
    raw["marks"] = {}
    (tmp_path / "portfolio.json").write_text(json.dumps(raw), encoding="utf-8")
    agent.portfolio = Portfolio(tmp_path / "portfolio.json", clock)  # posição sem nenhuma marca
    data = bus.register("data")

    async def main():
        await bus.start()
        data.publish("MarkPrice", {"ticker": "BBB", "price": "5"})  # outro ticker: AAA segue sem marca
        await bus.drain()
        await bus.stop()

    asyncio.run(main())
    assert [r.rule for r in halts.active()] == ["mark_ausente"]


def test_reset_request_rebases_peak_and_publishes(tmp_path, clock):
    bus, halts, portfolio, agent, views, risk_pub = agent_env(tmp_path, clock)
    portfolio.apply_fill("AAA", "buy", D("10"), D("100"))
    portfolio.set_mark("AAA", D("120"))
    portfolio.set_mark("AAA", D("100"))

    async def main():
        await bus.start()
        risk_pub.publish("RiskResetRequested", {"peak": True, "losses": True, "who": "ruan",
                                                "reset_id": "r1"})
        await bus.drain()
        await bus.stop()

    asyncio.run(main())
    assert D(portfolio.view()["peak_equity"]) == D(portfolio.view()["equity"])
    assert views[-1]["reset_id"] == "r1"


def test_closed_order_event_advances_applied_seq_after_the_orders_fills(tmp_path, clock):
    bus, halts, portfolio, agent, views, _ = agent_env(tmp_path, clock)
    execution = bus.register("execution")

    async def main():
        await bus.start()
        f = execution.publish("Fill", {"approval_id": "a1", "ticker": "AAA", "side": "buy",
                                       "qty": "10", "price": "100"})
        c = execution.publish("OrderClosed", {"approval_id": "a1", "status": "filled",
                                              "filled_qty": "10"})
        await bus.drain()
        await bus.stop()
        return f["seq"], c["seq"]

    fill_seq, closed_seq = asyncio.run(main())
    assert [v["applied_seq"] for v in views] == [fill_seq, closed_seq]
    assert D(views[-1]["positions"]["AAA"]["qty"]) == 10  # a foto que leva o seq do fechamento já tem o fill


def test_invalid_fill_and_invalid_mark_events_are_integrity_halts(tmp_path, clock):
    bus, halts, portfolio, agent, views, _ = agent_env(tmp_path, clock)
    execution = bus.register("execution")
    data = bus.register("data")

    async def main():
        await bus.start()
        execution.publish("Fill", {"approval_id": "a", "ticker": "AAA", "side": "buy",
                                   "qty": "nan", "price": "100"})
        data.publish("MarkPrice", {"ticker": "AAA", "price": "-3"})
        await bus.drain()
        await bus.stop()

    asyncio.run(main())
    assert sorted(r.rule for r in halts.active()) == ["fill_invalido", "mark_invalida"]
    assert views == [] and portfolio.view()["positions"] == {}


def test_announce_with_a_position_without_marks_is_a_halt_not_an_exception(tmp_path, clock):
    bus, halts, portfolio, agent, views, _ = agent_env(tmp_path, clock)
    portfolio.apply_fill("AAA", "buy", D("10"), D("100"))
    raw = json.loads((tmp_path / "portfolio.json").read_text(encoding="utf-8"))
    raw["marks"] = {}
    (tmp_path / "portfolio.json").write_text(json.dumps(raw), encoding="utf-8")
    agent.portfolio = Portfolio(tmp_path / "portfolio.json", clock)

    async def main():
        await bus.start()
        await agent.announce()
        await bus.stop()

    asyncio.run(main())
    assert [r.rule for r in halts.active()] == ["mark_ausente"] and views == []
