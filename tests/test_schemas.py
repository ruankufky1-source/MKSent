from datetime import datetime
from sentinela.schemas import (
    NewsClassification, Confidence, Action,
    AnalyzedNews, Candidate, RiskState,
)


def test_enums_have_expected_values():
    assert NewsClassification.DURABLE.value == "durable"
    assert NewsClassification.TRANSITORY.value == "transitory"
    assert Confidence.HIGH.value == "high"
    assert Action.BUY.value == "buy"


def test_analyzed_news_is_immutable():
    news = AnalyzedNews(
        ticker="AAPL",
        published_at=datetime(2026, 7, 20, 8, 0),
        classification=NewsClassification.DURABLE,
        sentiment=0.7,
        confidence=Confidence.HIGH,
        summary="Resultado trimestral acima do esperado",
    )
    assert news.ticker == "AAPL"
    try:
        news.ticker = "MSFT"  # frozen -> deve falhar
        assert False, "AnalyzedNews deveria ser imutável"
    except AttributeError:
        pass


def test_candidate_and_riskstate_construct():
    cand = Candidate(ticker="AAPL", action=Action.BUY, target_weight=0.05, reason="teste")
    assert cand.target_weight == 0.05
    state = RiskState(
        equity=10000.0, peak_equity=10000.0, daily_pnl=0.0, weekly_pnl=0.0,
        consecutive_losses=0, trades_today=0, last_trade_at=None, open_positions={},
    )
    assert state.equity == 10000.0
