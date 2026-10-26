from datetime import date

import pytest

from sentinela.trading.clock import SimClock


@pytest.fixture
def clock():
    return SimClock(date(2026, 7, 20))  # segunda-feira
