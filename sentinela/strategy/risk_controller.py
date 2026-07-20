from dataclasses import dataclass
from datetime import date
from sentinela.schemas import RiskState, Candidate


@dataclass(frozen=True)
class RiskConfig:
    max_daily_loss: float            # fração negativa, ex: -0.03
    max_weekly_loss: float           # fração negativa, ex: -0.06
    max_drawdown: float              # fração negativa, ex: -0.15
    max_consecutive_losses: int
    max_trades_per_day: int
    min_minutes_between_trades: int
    max_position_weight: float       # teto de concentração por ação, ex: 0.10
    earnings_blackout_days: int      # não abrir a <= N dias do resultado


@dataclass(frozen=True)
class RiskResult:
    approved: list[Candidate]
    rejected: list[tuple[Candidate, str]]   # (candidata, motivo do veto)
    halted: bool
    halt_reason: str | None


def _global_halt_reason(state: RiskState, cfg: RiskConfig) -> str | None:
    """Retorna o motivo se alguma trava global disparou, senão None."""
    if state.daily_pnl <= cfg.max_daily_loss:
        return f"perda diária no limite ({state.daily_pnl:.2%})"
    if state.weekly_pnl <= cfg.max_weekly_loss:
        return f"perda semanal no limite ({state.weekly_pnl:.2%})"
    drawdown = (state.equity - state.peak_equity) / state.peak_equity
    if drawdown <= cfg.max_drawdown:
        return f"drawdown no limite ({drawdown:.2%})"
    if state.consecutive_losses >= cfg.max_consecutive_losses:
        return f"perdas consecutivas no limite ({state.consecutive_losses})"
    if state.trades_today >= cfg.max_trades_per_day:
        return f"operações do dia no limite ({state.trades_today})"
    return None


def apply_risk(
    candidates: list[Candidate],
    state: RiskState,
    cfg: RiskConfig,
    earnings_calendar: dict[str, date],
    today: date,
) -> RiskResult:
    """Filtra candidatas contra travas globais e filtros por candidata.

    Travas globais disparadas param tudo (halted). Caso contrário, cada
    candidata passa pelos filtros de contexto (implementados na Task 5).
    """
    halt_reason = _global_halt_reason(state, cfg)
    if halt_reason is not None:
        return RiskResult(approved=[], rejected=[], halted=True, halt_reason=halt_reason)

    # Filtros por candidata entram na Task 5. Por ora, aprova todas.
    return RiskResult(approved=list(candidates), rejected=[], halted=False, halt_reason=None)
