import sqlite3
from datetime import datetime
from sentinela.schemas import AnalyzedNews, NewsClassification, Confidence


class AnalystCache:
    """Cache SQLite das classificações do Analista, chaveado por news_id."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS analyzed_news (
                news_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                published_at TEXT NOT NULL,
                classification TEXT NOT NULL,
                sentiment REAL NOT NULL,
                confidence TEXT NOT NULL,
                summary TEXT NOT NULL
            )"""
        )
        self.conn.commit()

    def get(self, news_id: str) -> AnalyzedNews | None:
        row = self.conn.execute(
            "SELECT ticker, published_at, classification, sentiment, confidence, summary "
            "FROM analyzed_news WHERE news_id = ?",
            (news_id,),
        ).fetchone()
        if row is None:
            return None
        ticker, published_at, classification, sentiment, confidence, summary = row
        return AnalyzedNews(
            ticker=ticker,
            published_at=datetime.fromisoformat(published_at),
            classification=NewsClassification(classification),
            sentiment=sentiment,
            confidence=Confidence(confidence),
            summary=summary,
        )

    def put(self, news_id: str, analyzed: AnalyzedNews) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO analyzed_news "
            "(news_id, ticker, published_at, classification, sentiment, confidence, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                news_id,
                analyzed.ticker,
                analyzed.published_at.isoformat(),
                analyzed.classification.value,
                analyzed.sentiment,
                analyzed.confidence.value,
                analyzed.summary,
            ),
        )
        self.conn.commit()
