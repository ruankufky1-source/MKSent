from dataclasses import dataclass
from datetime import date, datetime, timedelta
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

    approved: list[Candidate] = []
    rejected: list[tuple[Candidate, str]] = []
    # "agora" de referência: início do pregão no dia da rodada (08:00:30),
    # suficiente pra checagem de intervalo numa rodada diária de swing.
    now = datetime(today.year, today.month, today.day, 8, 0, 30)

    for cand in candidates:
        # 1. Blackout de earnings
        ed = earnings_calendar.get(cand.ticker)
        if ed is not None and 0 <= (ed - today).days <= cfg.earnings_blackout_days:
            rejected.append((cand, f"blackout de earnings ({ed.isoformat()})"))
            continue

        # 2. Teto de concentração: v1 não aumenta posição já existente
        if cand.ticker in state.open_positions:
            rejected.append((cand, "concentração: já há posição no ticker"))
            continue

        # 3. Intervalo mínimo entre operações
        if state.last_trade_at is not None:
            elapsed = now - state.last_trade_at
            if elapsed < timedelta(minutes=cfg.min_minutes_between_trades):
                rejected.append((cand, f"intervalo mínimo entre operações ({elapsed})"))
                continue

        # 4. Dimensionamento por drawdown: encolhe o peso conforme a perda real
        drawdown = (state.equity - state.peak_equity) / state.peak_equity
        factor = max(0.0, 1.0 - abs(drawdown) / abs(cfg.max_drawdown))
        scaled_weight = cand.target_weight * factor
        if scaled_weight <= 0.0:
            rejected.append((cand, "peso zerado pelo dimensionamento por drawdown"))
            continue

        # respeita o teto de concentração no peso final
        final_weight = min(scaled_weight, cfg.max_position_weight)
        approved.append(
            Candidate(
                ticker=cand.ticker,
                action=cand.action,
                target_weight=final_weight,
                reason=cand.reason,
            )
        )

    return RiskResult(approved=approved, rejected=rejected, halted=False, halt_reason=None)
