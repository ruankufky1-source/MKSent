from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class NewsClassification(str, Enum):
    DURABLE = "durable"        # fato novo sobre fluxo de caixa (segue o fato)
    TRANSITORY = "transitory"  # opinião/humor passageiro (reverte)


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Action(str, Enum):
    BUY = "buy"
    HOLD = "hold"


@dataclass(frozen=True)
class AnalyzedNews:
    ticker: str
    published_at: datetime
    classification: NewsClassification
    sentiment: float           # -1.0 (muito negativo) a 1.0 (muito positivo)
    confidence: Confidence
    summary: str


@dataclass(frozen=True)
class Candidate:
    ticker: str
    action: Action
    target_weight: float       # fração do capital (0.0 a 1.0)
    reason: str


@dataclass(frozen=True)
class RiskState:
    equity: float
    peak_equity: float         # maior equity já atingido (base do drawdown)
    daily_pnl: float           # resultado do dia, fração do equity (ex: -0.02)
    weekly_pnl: float          # resultado da semana, fração do equity
    consecutive_losses: int
    trades_today: int
    last_trade_at: datetime | None
    open_positions: dict       # ticker -> peso atual na carteira
