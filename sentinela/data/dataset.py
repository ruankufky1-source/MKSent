from dataclasses import dataclass
from datetime import date
from sentinela.data.schemas import PriceBar
from sentinela.data.providers import PriceProvider, NewsProvider, Analyst
from sentinela.schemas import AnalyzedNews


@dataclass(frozen=True)
class HistoricalDataset:
    prices: dict[str, list[PriceBar]]   # ticker -> barras diárias
    news: list[AnalyzedNews]            # todas as notícias já classificadas


def build_historical_dataset(
    tickers: list[str],
    start: date,
    end: date,
    price_provider: PriceProvider,
    news_provider: NewsProvider,
    analyst: Analyst,
) -> HistoricalDataset:
    """Monta o conjunto histórico: preços + notícias classificadas.

    As notícias de TODOS os tickers são classificadas numa única passada
    pelo Analista (preserva o batching, e o CachedAnalyst deduplica entre
    execuções). Ticker sem notícia não falha — entra com lista vazia.
    """
    prices: dict[str, list[PriceBar]] = {}
    all_raw = []
    for ticker in tickers:
        prices[ticker] = price_provider.get_bars(ticker, start, end)
        all_raw.extend(news_provider.get_news(ticker, start, end))

    classified = analyst.classify(all_raw) if all_raw else []
    return HistoricalDataset(prices=prices, news=classified)
