from datetime import datetime
from sentinela.data.cache import AnalystCache, CachedAnalyst
from sentinela.data.schemas import RawNews
from sentinela.schemas import AnalyzedNews, NewsClassification, Confidence


class CountingAnalyst:
    """Analista fake que conta chamadas e notícias vistas."""
    def __init__(self):
        self.call_count = 0
        self.seen_ids: list[str] = []

    def classify(self, news: list[RawNews]) -> list[AnalyzedNews]:
        self.call_count += 1
        self.seen_ids.extend(n.news_id for n in news)
        return [AnalyzedNews(ticker=n.ticker, published_at=n.published_at,
                             classification=NewsClassification.DURABLE, sentiment=0.5,
                             confidence=Confidence.HIGH, summary=n.headline) for n in news]


def _raw(nid, ticker="AAPL"):
    return RawNews(news_id=nid, ticker=ticker,
                   published_at=datetime(2026, 7, 20, 8, 0),
                   headline=f"headline {nid}", body="b", source="fake")


def test_first_pass_calls_inner_once_for_all(tmp_path):
    inner = CountingAnalyst()
    cached = CachedAnalyst(inner, AnalystCache(str(tmp_path / "c.db")))
    out = cached.classify([_raw("1"), _raw("2"), _raw("3")])
    assert len(out) == 3
    assert inner.call_count == 1          # uma única chamada em lote
    assert inner.seen_ids == ["1", "2", "3"]


def test_second_pass_hits_cache_and_skips_inner(tmp_path):
    inner = CountingAnalyst()
    cache = AnalystCache(str(tmp_path / "c.db"))
    cached = CachedAnalyst(inner, cache)
    batch = [_raw("1"), _raw("2"), _raw("3")]
    cached.classify(batch)                # popula o cache
    cached.classify(batch)                # tudo cache -> não chama o inner de novo
    assert inner.call_count == 1


def test_partial_cache_only_sends_misses(tmp_path):
    inner = CountingAnalyst()
    cache = AnalystCache(str(tmp_path / "c.db"))
    cached = CachedAnalyst(inner, cache)
    cached.classify([_raw("1"), _raw("2")])       # cacheia 1 e 2
    inner.seen_ids.clear()
    cached.classify([_raw("1"), _raw("2"), _raw("3")])  # só 3 é miss
    assert inner.seen_ids == ["3"]
    assert inner.call_count == 2                   # 1ª chamada + a dos misses


def test_output_preserves_input_order(tmp_path):
    inner = CountingAnalyst()
    cache = AnalystCache(str(tmp_path / "c.db"))
    cached = CachedAnalyst(inner, cache)
    cached.classify([_raw("1")])                   # 1 no cache
    out = cached.classify([_raw("2"), _raw("1"), _raw("3")])  # mistura cache e miss
    assert [o.summary for o in out] == ["headline 2", "headline 1", "headline 3"]
