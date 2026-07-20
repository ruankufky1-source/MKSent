from datetime import datetime
from sentinela.schemas import AnalyzedNews, NewsClassification, Confidence, Action
from sentinela.strategy.decision import decide, DecisionConfig


def _news(ticker, classification, sentiment, confidence):
    return AnalyzedNews(
        ticker=ticker, published_at=datetime(2026, 7, 20, 8, 0),
        classification=classification, sentiment=sentiment,
        confidence=confidence, summary="resumo",
    )


def test_durable_positive_high_confidence_becomes_buy():
    news = [_news("AAPL", NewsClassification.DURABLE, 0.7, Confidence.HIGH)]
    cands = decide(news, DecisionConfig())
    assert len(cands) == 1
    assert cands[0].ticker == "AAPL"
    assert cands[0].action == Action.BUY
    assert cands[0].target_weight == DecisionConfig().base_weight


def test_transitory_news_is_ignored():
    news = [_news("AAPL", NewsClassification.TRANSITORY, 0.9, Confidence.HIGH)]
    assert decide(news, DecisionConfig()) == []


def test_low_confidence_is_ignored():
    news = [_news("AAPL", NewsClassification.DURABLE, 0.7, Confidence.LOW)]
    assert decide(news, DecisionConfig()) == []


def test_sentiment_below_threshold_is_ignored():
    news = [_news("AAPL", NewsClassification.DURABLE, 0.1, Confidence.HIGH)]
    assert decide(news, DecisionConfig(min_sentiment=0.3)) == []


def test_negative_sentiment_never_buys():
    # long-only: sentimento negativo nunca vira ordem (não há short na v1)
    news = [_news("AAPL", NewsClassification.DURABLE, -0.8, Confidence.HIGH)]
    assert decide(news, DecisionConfig()) == []


def test_multiple_tickers_filtered_independently():
    news = [
        _news("AAPL", NewsClassification.DURABLE, 0.7, Confidence.HIGH),   # entra
        _news("MSFT", NewsClassification.TRANSITORY, 0.9, Confidence.HIGH),# ignora
        _news("NVDA", NewsClassification.DURABLE, 0.5, Confidence.MEDIUM), # entra
    ]
    cands = decide(news, DecisionConfig())
    assert {c.ticker for c in cands} == {"AAPL", "NVDA"}
