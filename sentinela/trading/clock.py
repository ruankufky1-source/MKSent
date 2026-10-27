from datetime import date, datetime, timedelta, timezone


SIM_HOUR_UTC = 15  # hora fixa do relógio de teste


class SimClock:
    """Relógio injetado: `now()` em UTC com fuso e `ny_date()` (data de Nova York).

    Na entrega 2 o relógio real vem de /v2/clock da Alpaca; este serve aos testes.
    """

    def __init__(self, ny_date: date):
        self._date = ny_date
        self._offset_s = 0

    def set_date(self, d: date) -> None:
        self._date = d

    def advance(self, seconds: float) -> None:
        self._offset_s += seconds

    def ny_date(self) -> date:
        return self._date

    def now(self) -> datetime:
        d = self._date
        return (datetime(d.year, d.month, d.day, SIM_HOUR_UTC, 0, tzinfo=timezone.utc)
                + timedelta(seconds=self._offset_s))


def week_key(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"
