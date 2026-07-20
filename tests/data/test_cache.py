from datetime import datetime
from sentinela.data.cache import AnalystCache
from sentinela.schemas import AnalyzedNews, NewsClassification, Confidence


def _analyzed(ticker="AAPL"):
    return AnalyzedNews(
        ticker=ticker, published_at=datetime(2026, 7, 20, 8, 0),
        classification=NewsClassification.DURABLE, sentiment=0.7,
        confidence=Confidence.HIGH, summary="lucro recorde",
    )


def test_put_then_get_roundtrip(tmp_path):
    cache = AnalystCache(str(tmp_path / "cache.db"))
    cache.put("id1", _analyzed())
    got = cache.get("id1")
    assert got == _analyzed()


def test_get_missing_returns_none(tmp_path):
    cache = AnalystCache(str(tmp_path / "cache.db"))
    assert cache.get("inexistente") is None


def test_persists_across_instances(tmp_path):
    db = str(tmp_path / "cache.db")
    AnalystCache(db).put("id1", _analyzed())
    # nova instância, mesmo arquivo -> dado sobrevive
    assert AnalystCache(db).get("id1") == _analyzed()


def test_put_is_idempotent(tmp_path):
    cache = AnalystCache(str(tmp_path / "cache.db"))
    cache.put("id1", _analyzed())
    cache.put("id1", _analyzed())  # regravar a mesma chave não deve falhar
    assert cache.get("id1") == _analyzed()
