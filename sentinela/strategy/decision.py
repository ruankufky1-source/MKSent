from dataclasses import dataclass
from sentinela.schemas import AnalyzedNews, Candidate, NewsClassification, Confidence, Action


@dataclass(frozen=True)
class DecisionConfig:
    min_sentiment: float = 0.3   # limiar positivo mínimo pra considerar compra
    base_weight: float = 0.05    # peso-alvo por posição (5% do capital)


def decide(news: list[AnalyzedNews], config: DecisionConfig) -> list[Candidate]:
    """Transforma notícias analisadas em candidatas de compra (long-only, v1).

    Só gera COMPRA para notícia durável, positiva acima do limiar e com
    confiança não-baixa. Notícia transitória e sentimento negativo são
    ignorados — a v1 não segue humor passageiro nem vende a descoberto.
    """
    candidates: list[Candidate] = []
    for n in news:
        if n.classification != NewsClassification.DURABLE:
            continue
        if n.confidence == Confidence.LOW:
            continue
        if n.sentiment < config.min_sentiment:
            continue
        candidates.append(
            Candidate(
                ticker=n.ticker,
                action=Action.BUY,
                target_weight=config.base_weight,
                reason=f"durável, sentimento {n.sentiment:.2f}: {n.summary}",
            )
        )
    return candidates
