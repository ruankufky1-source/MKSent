import asyncio
import json
from decimal import Decimal as D

from sentinela.strategy.risk_controller import RiskConfig
from sentinela.trading.audit import AuditLog, explain, read_log
from sentinela.trading.bus import EventBus
from sentinela.trading.halts import HaltStore
from sentinela.trading.portfolio import Portfolio, PortfolioAgent
from sentinela.trading.risk import AgentRiskConfig, Risk, RiskAgent


class System:
    def __init__(self, tmp_path, clock, cash="10000", max_drawdown=-0.15, max_daily_loss=-0.03,
                 max_weekly_loss=-0.06):
        self.tmp = tmp_path
        self.audit = AuditLog(tmp_path / "audit.jsonl", clock)
        self.halts = HaltStore(tmp_path / "halts.json", clock, audit=self.audit, first_run=True)
        self.bus = EventBus(self.audit, self.halts, clock, marker_path=tmp_path / "LOOP_TRAVADO")
        self.portfolio = Portfolio(tmp_path / "portfolio.json", clock, initial_cash=D(cash),
                                   first_run=True)
        self.portfolio_agent = PortfolioAgent(self.portfolio, self.bus)
        cfg = AgentRiskConfig(
            RiskConfig(max_daily_loss=max_daily_loss, max_weekly_loss=max_weekly_loss,
                       max_drawdown=max_drawdown,
                       max_consecutive_losses=4, max_trades_per_day=5,
                       min_minutes_between_trades=1, max_position_weight=0.10,
                       earnings_blackout_days=2),
            max_total_exposure=0.5, allowed_tickers=frozenset({"AAA", "BBB"}))
        self.risk = Risk(cfg, self.halts, tmp_path / "risk_state.json", clock,
                         tmp_path / "PARAR", first_run=True)
        self.risk_agent = RiskAgent(self.risk, self.bus)
        self.strategy = self.bus.register("strategy")
        self.data = self.bus.register("data")
        self.execution = self.bus.register("execution")
        self.seen = []

        async def spy(ev):
            self.seen.append((ev["type"], ev["payload"]))

        self.bus.register("spy", spy, subscribes=["OrderApproved", "OrderVetoed"])

    def signal(self, corr, ticker="AAA", weight="0.05", price="50"):
        return self.strategy.publish(
            "SignalProposed",
            {"side": "buy", "ticker": ticker, "target_weight": weight, "ref_price": price,
             "bar_id": "b1"}, correlation_id=corr)


def run(coro):
    return asyncio.run(coro)


def test_signal_to_approval_chain_is_explainable_from_the_file_alone(tmp_path, clock):
    s = System(tmp_path, clock)

    async def main():
        await s.bus.start()
        await s.portfolio_agent.announce()
        s.signal("sig-1")
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    s.audit.close()
    chain = explain(tmp_path / "audit.jsonl", "sig-1")
    assert [r["type"] for r in chain] == ["SignalProposed", "OrderApproved"]
    approved = chain[1]
    assert approved["causation_id"] == chain[0]["seq"] and approved["agent"] == "risk"
    assert D(approved["payload"]["qtd"]) == 10 and D(approved["payload"]["reserve"]) == 500
    assert approved["payload"]["inputs"]["equity"] == "10000"


def test_veto_is_logged_with_rule_and_values(tmp_path, clock):
    s = System(tmp_path, clock)

    async def main():
        await s.bus.start()
        await s.portfolio_agent.announce()
        s.signal("sig-2", ticker="ZZZ")
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    s.audit.close()
    chain = explain(tmp_path / "audit.jsonl", "sig-2")
    assert [r["type"] for r in chain] == ["SignalProposed", "OrderVetoed"]
    assert chain[1]["payload"]["rule"] == "ticker_fora_da_lista"
    assert chain[1]["payload"]["ticker"] == "ZZZ"
    assert [t for t, _ in s.seen] == ["OrderVetoed"]


def test_risk_crash_is_fail_closed_nothing_is_approved(tmp_path, clock, monkeypatch):
    s = System(tmp_path, clock)

    def boom(sig):
        raise RuntimeError("bug no risk")

    monkeypatch.setattr(s.risk, "decide", boom)

    async def main():
        await s.bus.start()
        await s.portfolio_agent.announce()
        s.signal("sig-3")
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    assert s.seen == []
    assert [r.rule for r in s.halts.active()] == ["agente_risk_caiu"]


def test_clear_halt_after_drawdown_rebases_peak_and_is_logged(tmp_path, clock):
    # limite de drawdown de 2% para que só ele dispare (uma queda maior no dia cai antes na perda diária)
    s = System(tmp_path, clock, max_drawdown=-0.02)

    async def main():
        await s.bus.start()
        s.execution.publish("Fill", {"approval_id": "x", "ticker": "AAA", "side": "buy",
                                     "qty": "40", "price": "100"})  # 4.000 em ações, equity 10.000
        s.data.publish("MarkPrice", {"ticker": "AAA", "price": "95"})  # equity 9.800: -2%
        await s.bus.drain()
        s.signal("sig-4", ticker="BBB")
        await s.bus.drain()
        assert [t for t, _ in s.seen] == ["OrderVetoed"] and s.seen[0][1]["rule"] == "drawdown"
        s.risk_agent.clear_halt("ruan", "conferi a conta")
        await s.bus.drain()
        s.signal("sig-5", ticker="BBB")
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    assert [t for t, _ in s.seen] == ["OrderVetoed", "OrderApproved"]
    assert D(s.portfolio.view()["peak_equity"]) == 9800
    s.audit.close()
    cleared = [r for r in read_log(tmp_path / "audit.jsonl") if r["type"] == "HaltCleared"]
    assert cleared[0]["payload"]["who"] == "ruan"
    assert cleared[0]["payload"]["reason"] == "conferi a conta"
    assert "drawdown" in cleared[0]["payload"]["rules"]


def test_order_closed_event_frees_reservation(tmp_path, clock):
    s = System(tmp_path, clock)

    async def main():
        await s.bus.start()
        await s.portfolio_agent.announce()
        s.signal("sig-6")
        await s.bus.drain()
        approval_id = s.seen[0][1]["approval_id"]
        s.execution.publish("OrderClosed", {"approval_id": approval_id, "status": "rejected",
                                            "filled_qty": "0"})
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    state = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    assert state["pending"] == {} and state["trades_today"] == 0


def _risk_state(tmp_path):
    return json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))


def test_clear_halt_of_kill_switch_keeps_the_peak_and_the_drawdown_memory(tmp_path, clock):
    s = System(tmp_path, clock, max_daily_loss=-0.5, max_weekly_loss=-0.5)

    async def main():
        await s.bus.start()
        s.execution.publish("Fill", {"approval_id": "x", "ticker": "AAA", "side": "buy",
                                     "qty": "40", "price": "100"})
        s.data.publish("MarkPrice", {"ticker": "AAA", "price": "90"})  # 9.600: -4% do pico
        await s.bus.drain()
        s.bus.kill("ruan", "teste")
        s.risk_agent.clear_halt("ruan", "ok")  # só o kill_switch estava ativo
        await s.bus.drain()
        s.signal("sig-k", ticker="BBB")
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    assert D(s.portfolio.view()["peak_equity"]) == 10000  # pico não foi refeito
    approved = [p for t, p in s.seen if t == "OrderApproved"]
    assert D(approved[0]["qtd"]) == 7  # com o fator de drawdown 0,73; sem ele seria 9


def test_clear_halt_of_consecutive_losses_resets_only_the_streak(tmp_path, clock):
    s = System(tmp_path, clock, max_daily_loss=-0.5, max_weekly_loss=-0.5)
    for _ in range(4):
        s.portfolio.apply_fill("AAA", "buy", D("10"), D("100"))
        s.portfolio.apply_fill("AAA", "sell", D("10"), D("90"))

    async def main():
        await s.bus.start()
        await s.portfolio_agent.announce()
        s.signal("a")
        await s.bus.drain()
        s.risk_agent.clear_halt("ruan", "revi as perdas")
        await s.bus.drain()
        s.signal("b")
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    assert [t for t, _ in s.seen] == ["OrderVetoed", "OrderApproved"]
    assert s.seen[0][1]["rule"] == "consecutive_losses"
    view = s.portfolio.view()
    assert view["consecutive_losses"] == 0 and D(view["peak_equity"]) == 10000


def test_signal_in_flight_during_clear_halt_never_rearms_the_cleared_halt(tmp_path, clock):
    s = System(tmp_path, clock, max_drawdown=-0.02)

    async def main():
        await s.bus.start()
        s.execution.publish("Fill", {"approval_id": "x", "ticker": "AAA", "side": "buy",
                                     "qty": "40", "price": "100"})
        s.data.publish("MarkPrice", {"ticker": "AAA", "price": "95"})  # 9.800: drawdown de 2%
        await s.bus.drain()
        s.signal("s1", ticker="BBB")
        await s.bus.drain()
        s.risk_agent.clear_halt("ruan", "ok")
        s.signal("s2", ticker="BBB")  # já na fila do Risk antes de a foto nova voltar
        await s.bus.drain()
        s.signal("s3", ticker="BBB")
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    rules = [p["rule"] for t, p in s.seen if t == "OrderVetoed"]
    assert rules[0] == "drawdown" and "drawdown" not in rules[1:]
    assert any(t == "OrderApproved" for t, _ in s.seen)


def test_publish_failure_after_reserving_releases_the_reservation(tmp_path, clock, monkeypatch):
    s = System(tmp_path, clock)

    def boom(raw):
        raise OSError("disco cheio")

    async def main():
        await s.bus.start()
        await s.portfolio_agent.announce()
        s.signal("x")  # este ainda grava
        monkeypatch.setattr(s.audit, "_write", boom)  # a aprovação não consegue ser gravada
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    state = _risk_state(tmp_path)
    assert state["pending"] == {} and state["trades_today"] == 0
    assert "agente_risk_caiu" in [r.rule for r in s.halts.active()]


def test_filled_order_is_released_only_after_the_portfolio_applied_its_fills(tmp_path, clock):
    s = System(tmp_path, clock)

    async def main():
        await s.bus.start()
        await s.portfolio_agent.announce()
        s.signal("sig-7")
        await s.bus.drain()
        approval_id = s.seen[0][1]["approval_id"]
        s.execution.publish("Fill", {"approval_id": approval_id, "ticker": "AAA", "side": "buy",
                                     "qty": "10", "price": "50"})
        s.execution.publish("OrderClosed", {"approval_id": approval_id, "status": "filled",
                                            "filled_qty": "10"})
        await s.bus.drain()
        await s.bus.stop()

    run(main())
    state = _risk_state(tmp_path)
    assert state["pending"] == {} and state["trades_today"] == 1
    assert D(s.portfolio.view()["positions"]["AAA"]["qty"]) == 10
