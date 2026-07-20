from datetime import datetime, date
from sentinela.data.schemas import PriceBar, RawNews
from sentinela.data.dataset import build_historical_dataset, HistoricalDataset
from sentinela.data.cache import AnalystCache, CachedAnalyst
from sentinela.schemas import AnalyzedNews, NewsClassification, Confidence


class FakePriceProvider:
    def get_bars(self, ticker, start, end):
        return [PriceBar(ticker=ticker, day=start, open=1.0, high=2.0,
                         low=0.5, close=1.5, volume=100)]


class FakeNewsProvider:
    def get_news(self, ticker, start, end):
        return [RawNews(news_id=f"{ticker}:1", ticker=ticker,
                        published_at=datetime(start.year, start.month, start.day, 8, 0),
                        headline=f"news {ticker}", body="b", source="fake")]


class CountingAnalyst:
    def __init__(self):
        self.call_count = 0

    def classify(self, news):
        self.call_count += 1
        return [AnalyzedNews(ticker=n.ticker, published_at=n.published_at,
                             classification=NewsClassification.DURABLE, sentiment=0.5,
                             confidence=Confidence.HIGH, summary=n.headline) for n in news]


def test_build_dataset_collects_prices_and_news():
    ds = build_historical_dataset(
        tickers=["AAPL", "MSFT"], start=date(2026, 1, 1), end=date(2026, 1, 31),
        price_provider=FakePriceProvider(), news_provider=FakeNewsProvider(),
        analyst=CountingAnalyst(),
    )
    assert isinstance(ds, HistoricalDataset)
    assert set(ds.prices.keys()) == {"AAPL", "MSFT"}
    assert len(ds.prices["AAPL"]) == 1
    assert {n.ticker for n in ds.news} == {"AAPL", "MSFT"}


def test_build_dataset_classifies_in_single_batch():
    analyst = CountingAnalyst()
    build_historical_dataset(
        tickers=["AAPL", "MSFT"], start=date(2026, 1, 1), end=date(2026, 1, 31),
        price_provider=FakePriceProvider(), news_provider=FakeNewsProvider(),
        analyst=analyst,
    )
    assert analyst.call_count == 1   # todas as notícias numa chamada só


def test_build_dataset_with_cache_skips_reclassification(tmp_path):
    inner = CountingAnalyst()
    analyst = CachedAnalyst(inner, AnalystCache(str(tmp_path / "c.db")))
    args = dict(tickers=["AAPL"], start=date(2026, 1, 1), end=date(2026, 1, 31),
                price_provider=FakePriceProvider(), news_provider=FakeNewsProvider())
    build_historical_dataset(analyst=analyst, **args)
    build_historical_dataset(analyst=analyst, **args)   # 2ª vez: cache cobre tudo
    assert inner.call_count == 1


def test_ticker_without_data_yields_empty_list():
    class EmptyNews:
        def get_news(self, ticker, start, end):
            return []
    ds = build_historical_dataset(
        tickers=["DEAD"], start=date(2026, 1, 1), end=date(2026, 1, 31),
        price_provider=FakePriceProvider(), news_provider=EmptyNews(),
        analyst=CountingAnalyst(),
    )
    assert ds.prices["DEAD"] == FakePriceProvider().get_bars("DEAD", date(2026,1,1), date(2026,1,31))
    assert ds.news == []
