from dataclasses import dataclass
from datetime import datetime, date


@dataclass(frozen=True)
class PriceBar:
    ticker: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class RawNews:
    news_id: str          # id estável da fonte — chave de deduplicação e de cache
    ticker: str
    published_at: datetime
    headline: str
    body: str
    source: str
