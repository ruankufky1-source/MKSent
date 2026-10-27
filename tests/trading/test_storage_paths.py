from datetime import date
from decimal import Decimal as D

import pytest

from sentinela.strategy.risk_controller import RiskConfig
from sentinela.trading.audit import AuditLog
from sentinela.trading.clock import SimClock
from sentinela.trading.halts import HaltStore
from sentinela.trading.portfolio import Portfolio
from sentinela.trading.risk import AgentRiskConfig, Risk


def _cfg():
    base = RiskConfig(max_daily_loss=-0.03, max_weekly_loss=-0.06, max_drawdown=-0.15,
                      max_consecutive_losses=4, max_trades_per_day=5, min_minutes_between_trades=1,
                      max_position_weight=0.10, earnings_blackout_days=2)
    return AgentRiskConfig(base, 0.5, frozenset({"AAA"}))


@pytest.mark.parametrize("make", [
    lambda c: AuditLog("audit.jsonl", c),
    lambda c: HaltStore("halts.json", c, first_run=True),
    lambda c: Portfolio("portfolio.json", c, initial_cash=D("1"), first_run=True),
    lambda c: Risk(_cfg(), None, "risk_state.json", c, "/tmp/PARAR", first_run=True),
])
def test_relative_state_paths_are_refused(make, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # um caminho relativo cairia na pasta de trabalho, não na de estado
    with pytest.raises(ValueError):
        make(SimClock(date(2026, 7, 20)))
    assert list(tmp_path.iterdir()) == []  # nada foi criado
