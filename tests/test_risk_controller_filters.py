from datetime import date, datetime
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


def _cand(ticker="AAPL", weight=0.05):
    return Candidate(ticker=ticker, action=Action.BUY, target_weight=weight, reason="teste")


def _apply(cands, state, cfg, earnings=None, today=date(2026, 7, 20)):
    return apply_risk(cands, state, cfg, earnings_calendar=earnings or {}, today=today)


def test_earnings_blackout_vetoes_candidate():
    # earnings em 2026-07-21, a 1 dia -> dentro do blackout de 2 dias
    result = _apply([_cand("AAPL")], _state(), _cfg(),
                    earnings={"AAPL": date(2026, 7, 21)})
    assert result.approved == []
    assert len(result.rejected) == 1
    assert "earnings" in result.rejected[0][1]


def test_earnings_far_away_is_allowed():
    result = _apply([_cand("AAPL")], _state(), _cfg(),
                    earnings={"AAPL": date(2026, 8, 30)})
    assert len(result.approved) == 1


def test_existing_position_vetoed_by_concentration():
    # já tem AAPL na carteira -> v1 não aumenta posição existente
    result = _apply([_cand("AAPL")], _state(open_positions={"AAPL": 0.05}), _cfg())
    assert result.approved == []
    assert "concentração" in result.rejected[0][1]


def test_min_interval_between_trades_vetoes():
    last = datetime(2026, 7, 20, 8, 0, 0)
    # nova ordem 30s depois, intervalo mínimo de 1 min -> veta
    state = _state(last_trade_at=last)
    result = apply_risk([_cand("AAPL")], state, _cfg(min_minutes_between_trades=1),
                        earnings_calendar={}, today=date(2026, 7, 20))
    # apply_risk usa last_trade_at + intervalo; "agora" é derivado de today às 08:00:30
    assert result.approved == []
    assert "intervalo" in result.rejected[0][1]


def test_drawdown_scales_down_weight():
    # drawdown atual -7.5% com max -15% -> fator 0.5 -> peso 0.05 vira 0.025
    state = _state(equity=9250.0, peak_equity=10000.0)
    result = _apply([_cand("AAPL", weight=0.05)], state, _cfg())
    assert len(result.approved) == 1
    assert abs(result.approved[0].target_weight - 0.025) < 1e-9


def test_no_drawdown_keeps_full_weight():
    result = _apply([_cand("AAPL", weight=0.05)], _state(), _cfg())
    assert abs(result.approved[0].target_weight - 0.05) < 1e-9
