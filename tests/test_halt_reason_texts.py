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


def _cfg():
    return RiskConfig(
        max_daily_loss=-0.03, max_weekly_loss=-0.06, max_drawdown=-0.15,
        max_consecutive_losses=4, max_trades_per_day=5, min_minutes_between_trades=1,
        max_position_weight=0.10, earnings_blackout_days=2,
    )


def _reason(**kw):
    cand = Candidate(ticker="AAPL", action=Action.BUY, target_weight=0.05, reason="teste")
    return apply_risk([cand], _state(**kw), _cfg(), earnings_calendar={},
                      today=date(2026, 7, 20)).halt_reason


def test_daily_loss_text():
    assert _reason(daily_pnl=-0.03) == "perda diária no limite (-3.00%)"


def test_weekly_loss_text():
    assert _reason(weekly_pnl=-0.07) == "perda semanal no limite (-7.00%)"


def test_drawdown_text():
    assert _reason(equity=8000.0) == "drawdown no limite (-20.00%)"


def test_consecutive_losses_text():
    assert _reason(consecutive_losses=4) == "perdas consecutivas no limite (4)"


def test_trades_per_day_text():
    assert _reason(trades_today=5) == "operações do dia no limite (5)"


def test_precedence_daily_before_weekly_before_drawdown():
    # várias travas ao mesmo tempo: vale a primeira da ordem diária > semanal > drawdown > perdas > trades
    assert _reason(daily_pnl=-0.04, weekly_pnl=-0.07, equity=8000.0,
                   consecutive_losses=4, trades_today=5) == "perda diária no limite (-4.00%)"
    assert _reason(weekly_pnl=-0.07, equity=8000.0,
                   consecutive_losses=4, trades_today=5) == "perda semanal no limite (-7.00%)"
    assert _reason(equity=8000.0, consecutive_losses=4,
                   trades_today=5) == "drawdown no limite (-20.00%)"
    assert _reason(consecutive_losses=4, trades_today=5) == "perdas consecutivas no limite (4)"
