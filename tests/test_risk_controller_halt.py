from datetime import date
from sentinela.schemas import RiskState, Candidate, Action
from sentinela.strategy.risk_controller import apply_risk, RiskConfig


def _state(**kw):
    base = dict(
        equity=10000.0, peak_equity=10000.0, daily_pnl=0.0, weekly_pnl=0.0,
        consecutive_losses=0, trades_today=0, last_trade_at=None, open_positions={},
    )
    base.update(kw)
    return RiskState(**base)


def _cfg(**kw):
    base = dict(
        max_daily_loss=-0.03, max_weekly_loss=-0.06, max_drawdown=-0.15,
        max_consecutive_losses=4, max_trades_per_day=5, min_minutes_between_trades=1,
        max_position_weight=0.10, earnings_blackout_days=2,
    )
    base.update(kw)
    return RiskConfig(**base)


def _cand(ticker="AAPL"):
    return Candidate(ticker=ticker, action=Action.BUY, target_weight=0.05, reason="teste")


def test_daily_loss_limit_halts_everything():
    result = apply_risk([_cand()], _state(daily_pnl=-0.03), _cfg(),
                        earnings_calendar={}, today=date(2026, 7, 20))
    assert result.halted is True
    assert result.approved == []
    assert "diária" in result.halt_reason


def test_weekly_loss_limit_halts():
    result = apply_risk([_cand()], _state(weekly_pnl=-0.07), _cfg(),
                        earnings_calendar={}, today=date(2026, 7, 20))
    assert result.halted is True


def test_drawdown_limit_halts():
    # equity 8000 vs pico 10000 = -20% drawdown, além do -15%
    result = apply_risk([_cand()], _state(equity=8000.0, peak_equity=10000.0), _cfg(),
                        earnings_calendar={}, today=date(2026, 7, 20))
    assert result.halted is True
    assert "drawdown" in result.halt_reason


def test_consecutive_losses_halts():
    result = apply_risk([_cand()], _state(consecutive_losses=4), _cfg(),
                        earnings_calendar={}, today=date(2026, 7, 20))
    assert result.halted is True


def test_max_trades_per_day_halts():
    result = apply_risk([_cand()], _state(trades_today=5), _cfg(),
                        earnings_calendar={}, today=date(2026, 7, 20))
    assert result.halted is True


def test_no_halt_when_all_within_limits():
    result = apply_risk([_cand()], _state(), _cfg(),
                        earnings_calendar={}, today=date(2026, 7, 20))
    assert result.halted is False
    assert len(result.approved) == 1
