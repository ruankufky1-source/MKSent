from datetime import date, datetime
from sentinela.schemas import (
    AnalyzedNews, RiskState, NewsClassification, Confidence,
)
from sentinela.strategy.decision import DecisionConfig
from sentinela.strategy.risk_controller import RiskConfig
from sentinela.strategy.pipeline import run_pipeline


def _news(ticker, classification, sentiment, confidence):
    return AnalyzedNews(
        ticker=ticker, published_at=datetime(2026, 7, 20, 8, 0),
        classification=classification, sentiment=sentiment,
        confidence=confidence, summary="resumo",
    )


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


def test_pipeline_end_to_end_mixed_batch():
    news = [
        _news("AAPL", NewsClassification.DURABLE, 0.7, Confidence.HIGH),    # compra
        _news("MSFT", NewsClassification.TRANSITORY, 0.9, Confidence.HIGH), # decisão ignora
        _news("NVDA", NewsClassification.DURABLE, 0.6, Confidence.HIGH),    # vetada: earnings
    ]
    result = run_pipeline(
        news=news, state=_state(), decision_cfg=DecisionConfig(), risk_cfg=_cfg(),
        earnings_calendar={"NVDA": date(2026, 7, 21)}, today=date(2026, 7, 20),
    )
    assert result.halted is False
    assert [c.ticker for c in result.approved] == ["AAPL"]


def test_pipeline_halts_on_global_limit():
    news = [_news("AAPL", NewsClassification.DURABLE, 0.7, Confidence.HIGH)]
    result = run_pipeline(
        news=news, state=_state(daily_pnl=-0.05), decision_cfg=DecisionConfig(),
        risk_cfg=_cfg(), earnings_calendar={}, today=date(2026, 7, 20),
    )
    assert result.halted is True
    assert result.approved == []
