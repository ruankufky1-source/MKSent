from datetime import datetime, date
from sentinela.data.schemas import PriceBar, RawNews


def test_price_bar_construct_and_immutable():
    bar = PriceBar(ticker="AAPL", day=date(2026, 7, 20),
                   open=190.0, high=192.5, low=189.0, close=191.2, volume=1_000_000)
    assert bar.close == 191.2
    try:
        bar.close = 200.0
        assert False, "PriceBar deveria ser imutável"
    except AttributeError:
        pass


def test_raw_news_carries_stable_id():
    news = RawNews(
        news_id="benzinga:12345", ticker="AAPL",
        published_at=datetime(2026, 7, 20, 8, 0),
        headline="Apple reporta lucro recorde", body="Texto completo...",
        source="benzinga",
    )
    assert news.news_id == "benzinga:12345"
    assert news.ticker == "AAPL"
