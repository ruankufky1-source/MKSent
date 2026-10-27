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


# Nomes das regras de parada: um lugar só (usados também pelo agente Risk)
DAILY_LOSS, WEEKLY_LOSS, DRAWDOWN = "daily_loss", "weekly_loss", "drawdown"
CONSECUTIVE_LOSSES, TRADES = "consecutive_losses", "trades"


@dataclass(frozen=True)
class Halt:
    rule: str       # uma das constantes acima
    value: float    # valor cru que disparou a regra (fração, ou contagem)


def global_halt(state: RiskState, cfg: RiskConfig) -> Halt | None:
    """Primeira trava global disparada, na ordem diária > semanal > drawdown > perdas > trades."""
    if state.daily_pnl <= cfg.max_daily_loss:
        return Halt(DAILY_LOSS, state.daily_pnl)
    if state.weekly_pnl <= cfg.max_weekly_loss:
        return Halt(WEEKLY_LOSS, state.weekly_pnl)
    drawdown = (state.equity - state.peak_equity) / state.peak_equity
    if drawdown <= cfg.max_drawdown:
        return Halt(DRAWDOWN, drawdown)
    if state.consecutive_losses >= cfg.max_consecutive_losses:
        return Halt(CONSECUTIVE_LOSSES, state.consecutive_losses)
    if state.trades_today >= cfg.max_trades_per_day:
        return Halt(TRADES, state.trades_today)
    return None


_HALT_TEXT = {
    DAILY_LOSS: "perda diária no limite ({:.2%})",
    WEEKLY_LOSS: "perda semanal no limite ({:.2%})",
    DRAWDOWN: "drawdown no limite ({:.2%})",
    CONSECUTIVE_LOSSES: "perdas consecutivas no limite ({})",
    TRADES: "operações do dia no limite ({})",
}


def _global_halt_reason(state: RiskState, cfg: RiskConfig) -> str | None:
    """Retorna o motivo se alguma trava global disparou, senão None."""
    halt = global_halt(state, cfg)
    if halt is None:
        return None
    return _HALT_TEXT[halt.rule].format(halt.value)


def drawdown_factor(equity: float, peak_equity: float, max_drawdown: float) -> float:
    """Fator 1.0 → 0.0 que encolhe o peso conforme o drawdown real se aproxima do limite."""
    drawdown = (equity - peak_equity) / peak_equity
    return max(0.0, 1.0 - abs(drawdown) / abs(max_drawdown))


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
        factor = drawdown_factor(state.equity, state.peak_equity, cfg.max_drawdown)
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
