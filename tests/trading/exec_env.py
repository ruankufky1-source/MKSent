"""Montagem do sistema inteiro com Execution + SimBroker para os testes da 1B."""
from decimal import Decimal as D

from sentinela.strategy.risk_controller import RiskConfig
from sentinela.trading.audit import AuditLog, read_log
from sentinela.trading.broker import GuardConfig, GuardedBroker
from sentinela.trading.bus import EventBus
from sentinela.trading.execution import Execution
from sentinela.trading.halts import HaltStore
from sentinela.trading.portfolio import Portfolio, PortfolioAgent
from sentinela.trading.risk import AgentRiskConfig, Risk, RiskAgent
from sentinela.trading.simbroker import SimBroker
from tests.trading.shared_loop import run  # noqa: F401 - reexportado para os testes

TIMEOUT = 0.05   # BROKER_TIMEOUT_S nos testes: o sim "dorme" 0,5 s quando falha


def risk_cfg():
    return AgentRiskConfig(
        RiskConfig(max_daily_loss=-0.03, max_weekly_loss=-0.06, max_drawdown=-0.15,
                   max_consecutive_losses=4, max_trades_per_day=5, min_minutes_between_trades=1,
                   max_position_weight=0.10, earnings_blackout_days=2),
        max_total_exposure=0.5, allowed_tickers=frozenset({"AAA", "BBB"}))


class Rig:
    def __init__(self, tmp_path, clock, *, sim=None, guard_cfg=None, cash="10000", first_run=True,
                 reconcile_ready=True):
        self.tmp, self.clock = tmp_path, clock
        self.sim = sim or SimBroker(seed=1, partial_prob=0, stall_s=0.5)
        self.guard_cfg = guard_cfg or GuardConfig(D("100000"), D("1000"), frozenset({"AAA", "BBB"}))
        self.cash = cash
        self.build(first_run)
        self.reconcile_ready = reconcile_ready

    def build(self, first_run):
        t, clock = self.tmp, self.clock
        self.audit = AuditLog(t / "audit.jsonl", clock)
        self.halts = HaltStore(t / "halts.json", clock, audit=self.audit, first_run=first_run)
        self.bus = EventBus(self.audit, self.halts, clock, marker_path=t / "LOOP_TRAVADO")
        self.portfolio = Portfolio(t / "portfolio.json", clock, initial_cash=D(self.cash),
                                   first_run=first_run)
        self.portfolio_agent = PortfolioAgent(self.portfolio, self.bus)
        self.risk = Risk(risk_cfg(), self.halts, t / "risk_state.json", clock, t / "PARAR",
                         first_run=first_run)
        self.risk_agent = RiskAgent(self.risk, self.bus)
        self.strategy = self.bus.register("strategy")
        self.data = self.bus.register("data")
        self.guard = GuardedBroker(self.sim, self.guard_cfg)
        self.execution = Execution(
            self.bus, self.guard, t / "execution_state.json", clock,
            positions=lambda: {k: p["qty"] for k, p in self.portfolio.positions.items()},
            kill_path=t / "PARAR", first_run=first_run, broker_timeout=TIMEOUT)

    async def start(self, reconcile=True):
        await self.bus.start()
        await self.portfolio_agent.announce()
        if reconcile:
            await self.execution.reconcile()
        await self.bus.drain()

    async def stop(self):
        await self.bus.stop()

    def restart(self, first_run=False):
        """Queda e volta: o log, os arquivos e a corretora (sim) continuam; a memória some."""
        self.audit.close()
        self.build(first_run)

    async def settle(self, rounds=3):
        """Drena o bus e consulta a corretora até não mudar mais nada."""
        for _ in range(rounds):
            await self.bus.drain()
            await self.execution.poll_once()
        await self.bus.drain()

    # --- aprovações ---------------------------------------------------------------------------

    def approve(self, ticker="AAA", side="buy", qtd="10", price="50", seq=1, corr=None):
        """Aprovação feita pelo Risk de verdade (reserva gravada) e publicada com o handle dele."""
        if side == "buy":
            payload = {"side": "buy", "ticker": ticker, "target_weight": str(D(qtd) * D(price) / 10000),
                       "ref_price": price, "bar_id": "b1"}
        else:
            payload = {"side": "sell", "ticker": ticker, "qtd": qtd, "bar_id": "b1"}
        kind, out = self.risk.decide({"seq": seq, "correlation_id": corr or f"c{seq}", "payload": payload})
        assert kind == "approved", out
        return self.risk_agent.pub.publish("OrderApproved", out, correlation_id=corr or f"c{seq}",
                                           causation_id=seq)

    def forge(self, ticker="AAA", side="buy", qtd="10", price="50", approval_id="forjada", corr="forjada"):
        """Aprovação publicada sem passar pelo Risk (a reserva dele não existe): para testar o Guard."""
        return self.risk_agent.pub.publish("OrderApproved", {
            "approval_id": approval_id, "side": side, "ticker": ticker, "qtd": qtd,
            "ref_price": price, "reserve": "0", "inputs": {}}, correlation_id=corr)

    # --- leitura ------------------------------------------------------------------------------

    def log(self, *types):
        recs = read_log(self.tmp / "audit.jsonl")
        return [r for r in recs if not types or r["type"] in types]

    def state(self):
        import json
        return json.loads((self.tmp / "execution_state.json").read_text(encoding="utf-8"))

    def risk_state(self):
        import json
        return json.loads((self.tmp / "risk_state.json").read_text(encoding="utf-8"))

    def rules(self):
        return sorted(r.rule for r in self.halts.active())
