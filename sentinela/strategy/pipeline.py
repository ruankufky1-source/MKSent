from datetime import date
from sentinela.schemas import AnalyzedNews, RiskState
from sentinela.strategy.decision import decide, DecisionConfig
from sentinela.strategy.risk_controller import apply_risk, RiskConfig, RiskResult


def run_pipeline(
    news: list[AnalyzedNews],
    state: RiskState,
    decision_cfg: DecisionConfig,
    risk_cfg: RiskConfig,
    earnings_calendar: dict[str, date],
    today: date,
) -> RiskResult:
    """Encadeia decisão + risco: notícias analisadas -> candidatas aprovadas.

    É o contrato único que o backtest e o robô ao vivo consomem — os dois
    passam pela MESMA lógica, garantindo que o validado é o que roda.
    """
    candidates = decide(news, decision_cfg)
    return apply_risk(candidates, state, risk_cfg, earnings_calendar, today)
