from datetime import datetime, date
from sentinela.data.schemas import PriceBar, RawNews
from sentinela.data.providers import PriceProvider, NewsProvider, Analyst
from sentinela.schemas import AnalyzedNews, NewsClassification, Confidence


class FakePriceProvider:
    def get_bars(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        return [PriceBar(ticker=ticker, day=start, open=1.0, high=2.0,
                         low=0.5, close=1.5, volume=100)]


class FakeNewsProvider:
    def get_news(self, ticker: str, start: date, end: date) -> list[RawNews]:
        return [RawNews(news_id=f"{ticker}:1", ticker=ticker,
                        published_at=datetime(start.year, start.month, start.day, 8, 0),
                        headline="h", body="b", source="fake")]


class FakeAnalyst:
    def classify(self, news: list[RawNews]) -> list[AnalyzedNews]:
        return [AnalyzedNews(ticker=n.ticker, published_at=n.published_at,
                             classification=NewsClassification.DURABLE, sentiment=0.5,
                             confidence=Confidence.HIGH, summary=n.headline) for n in news]


def test_fakes_satisfy_protocols():
    # runtime_checkable: isinstance confirma que os fakes cumprem os contratos
    assert isinstance(FakePriceProvider(), PriceProvider)
    assert isinstance(FakeNewsProvider(), NewsProvider)
    assert isinstance(FakeAnalyst(), Analyst)


def test_fake_providers_return_expected_shapes():
    bars = FakePriceProvider().get_bars("AAPL", date(2026, 1, 1), date(2026, 1, 2))
    assert isinstance(bars[0], PriceBar)
    news = FakeNewsProvider().get_news("AAPL", date(2026, 1, 1), date(2026, 1, 2))
    assert isinstance(news[0], RawNews)
    analyzed = FakeAnalyst().classify(news)
    assert isinstance(analyzed[0], AnalyzedNews)
    assert analyzed[0].ticker == "AAPL"
