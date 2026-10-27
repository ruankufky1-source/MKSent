import pytest
from sentinela.strategy.risk_controller import drawdown_factor


@pytest.mark.parametrize("equity, expected", [
    (10000.0, 1.0),    # sem drawdown
    (9250.0, 0.5),     # -7,5% com limite -15%
    (8500.0, 0.0),     # -15% no limite
    (8000.0, 0.0),     # -20%, além do limite: nunca negativo
])
def test_drawdown_factor(equity, expected):
    assert drawdown_factor(equity, 10000.0, -0.15) == pytest.approx(expected)
