from typing import Protocol, runtime_checkable
from datetime import date
from sentinela.data.schemas import PriceBar, RawNews
from sentinela.schemas import AnalyzedNews


@runtime_checkable
class PriceProvider(Protocol):
    def get_bars(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        """Barras diárias OHLCV do ticker no intervalo [start, end]."""
        ...


@runtime_checkable
class NewsProvider(Protocol):
    def get_news(self, ticker: str, start: date, end: date) -> list[RawNews]:
        """Notícias brutas do ticker no intervalo [start, end]."""
        ...


@runtime_checkable
class Analyst(Protocol):
    def classify(self, news: list[RawNews]) -> list[AnalyzedNews]:
        """Classifica notícias. Contrato: retorna a MESMA quantidade e na
        MESMA ordem da entrada (o CachedAnalyst depende disso)."""
        ...
