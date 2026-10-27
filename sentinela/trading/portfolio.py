from decimal import Decimal as D, InvalidOperation

from sentinela.trading import store
from sentinela.trading.clock import week_key
from sentinela.trading.events import (
    FILL, MARK_PRICE, ORDER_CLOSED, PORTFOLIO_UPDATED, RISK_RESET_REQUESTED,
)
from sentinela.trading.halts import INTEGRITY
from sentinela.trading.store import StateCorrupt, StateWriteError, require_absolute


class PortfolioCorrupt(Exception):
    """Fill que deixaria quantidade negativa (venda maior que a posição)."""


class InvalidFill(Exception):
    """Fill ou marca com lado desconhecido, número ausente ou não finito, quantidade ou preço <= 0."""


class MarkMissing(Exception):
    """Posição aberta sem nenhuma marca de preço: equity não é confiável."""


class PortfolioStateMissing(Exception):
    pass


class PortfolioStateCorrupt(Exception):
    pass


def _positive(x) -> bool:
    return isinstance(x, D) and x.is_finite() and x > 0


def _dec(x) -> D:
    try:
        return D(str(x))
    except InvalidOperation as e:
        raise InvalidFill(f"número inválido: {x!r}") from e


class Portfolio:
    """Posições (quantidade, preço médio) em Decimal, caixa, equity e os campos do estado de risco."""

    def __init__(self, path, clock, initial_cash: D | None = None, first_run: bool = False):
        self.path = require_absolute(path)
        self.clock = clock
        if not self.path.exists():
            if not first_run:
                raise PortfolioStateMissing(str(self.path))
            self._fresh(initial_cash)
        else:
            self._load()

    def _fresh(self, initial_cash: D | None) -> None:
        if initial_cash is None:
            raise ValueError("first_run exige initial_cash")
        self.cash = D(initial_cash)
        self.positions: dict[str, dict] = {}   # ticker -> {"qty": D, "avg": D}
        self.cycle: dict[str, D] = {}          # realizado acumulado do ciclo aberto
        self.marks: dict[str, dict] = {}       # ticker -> {"price": D, "ts": str}
        self.losses = 0
        self.reset_id = None                   # último pedido de clear_halt aplicado
        self.peak = None
        self.last_equity = None
        self.day_key = self.week_key = None
        self.day_base = self.week_base = None
        self._touch()
        self._save()

    # --- persistência ---------------------------------------------------------------------

    def _load(self) -> None:
        try:
            o = store.read_json(self.path)
            self.cash = D(o["cash"])
            self.positions = {t: {"qty": D(p["qty"]), "avg": D(p["avg"])}
                              for t, p in o["positions"].items()}
            self.cycle = {t: D(v) for t, v in o["cycle"].items()}
            self.marks = {t: {"price": D(m["price"]), "ts": m["ts"]} for t, m in o["marks"].items()}
            self.losses = int(o["losses"])
            self.reset_id = o.get("reset_id")
            opt = lambda k: None if o[k] is None else D(o[k])  # noqa: E731
            self.peak, self.last_equity = opt("peak"), opt("last_equity")
            self.day_base, self.week_base = opt("day_base"), opt("week_base")
            self.day_key, self.week_key = o["day_key"], o["week_key"]
        except (StateCorrupt, KeyError, TypeError, ValueError, ArithmeticError) as e:
            raise PortfolioStateCorrupt(str(e)) from e

    def _save(self) -> None:
        s = lambda x: None if x is None else str(x)  # noqa: E731
        store.atomic_write_json(self.path, {
            "cash": str(self.cash),
            "positions": {t: {"qty": str(p["qty"]), "avg": str(p["avg"])}
                          for t, p in self.positions.items()},
            "cycle": {t: str(v) for t, v in self.cycle.items()},
            "marks": {t: {"price": str(m["price"]), "ts": m["ts"]} for t, m in self.marks.items()},
            "losses": self.losses, "reset_id": self.reset_id,
            "peak": s(self.peak), "last_equity": s(self.last_equity),
            "day_base": s(self.day_base), "week_base": s(self.week_base),
            "day_key": self.day_key, "week_key": self.week_key,
        })

    # --- contas ---------------------------------------------------------------------------

    def equity(self) -> D:
        total = self.cash
        for t, p in self.positions.items():
            if t not in self.marks:
                raise MarkMissing(t)
            total += p["qty"] * self.marks[t]["price"]
        return total

    def _touch(self) -> bool:
        """Vira dia/semana com o último equity gravado e atualiza equity e pico. True se mudou."""
        eq = self.equity()
        before = (self.last_equity, self.peak, self.day_key, self.week_key)
        day, week = self.clock.ny_date().isoformat(), week_key(self.clock.ny_date())
        if self.day_key != day:
            self.day_base = self.last_equity if self.last_equity is not None else eq
            self.day_key = day
        if self.week_key != week:
            self.week_base = self.last_equity if self.last_equity is not None else eq
            self.week_key = week
        self.last_equity = eq
        self.peak = eq if self.peak is None else max(self.peak, eq)
        return before != (self.last_equity, self.peak, self.day_key, self.week_key)

    def apply_fill(self, ticker: str, side: str, qty: D, price: D) -> D:
        if side not in ("buy", "sell") or not _positive(qty) or not _positive(price):
            raise InvalidFill(f"fill inválido: {side!r} {qty!r} @ {price!r}")
        pos = self.positions.get(ticker, {"qty": D(0), "avg": D(0)})
        realized = D(0)
        if side == "buy":
            new_qty = pos["qty"] + qty
            avg = (pos["qty"] * pos["avg"] + qty * price) / new_qty
            self.positions[ticker] = {"qty": new_qty, "avg": avg}
            self.cash -= qty * price
        else:
            if qty > pos["qty"]:
                raise PortfolioCorrupt(f"venda de {qty} {ticker} com posição {pos['qty']}")
            realized = qty * (price - pos["avg"])
            self.cash += qty * price
            self.cycle[ticker] = self.cycle.get(ticker, D(0)) + realized
            left = pos["qty"] - qty
            if left == 0:
                self.losses = self.losses + 1 if self.cycle.pop(ticker) < 0 else 0
                del self.positions[ticker]
            else:
                self.positions[ticker] = {"qty": left, "avg": pos["avg"]}
        self.marks.setdefault(ticker, {"price": price, "ts": self.clock.now().isoformat()})
        self._touch()
        self._save()
        return realized

    def set_mark(self, ticker: str, price: D) -> None:
        if not _positive(price):
            raise InvalidFill(f"marca inválida: {price!r}")
        self.marks[ticker] = {"price": price, "ts": self.clock.now().isoformat()}
        self._touch()
        self._save()

    def rebase(self, peak: bool = True, losses: bool = True, reset_id: str | None = None) -> None:
        """Depois de clear_halt: refaz o pico (se o drawdown foi limpo) e zera a sequência de perdas
        (se as perdas seguidas foram limpas). `reset_id` marca a foto que já reflete o pedido."""
        if peak:
            self.peak = self.equity()
        if losses:
            self.losses = 0
        if reset_id is not None:
            self.reset_id = reset_id
        self._save()

    def view(self) -> dict:
        if self._touch():
            self._save()
        eq = self.equity()
        pnl = lambda base: D(0) if not base else (eq - base) / base  # noqa: E731
        return {
            "session_date": self.day_key,
            "cash": str(self.cash),
            "equity": str(eq),
            "peak_equity": str(self.peak),
            "daily_pnl": str(pnl(self.day_base)),
            "weekly_pnl": str(pnl(self.week_base)),
            "consecutive_losses": self.losses,
            "reset_id": self.reset_id,
            "positions": {t: {"qty": str(p["qty"]), "avg": str(p["avg"]),
                              "mark": str(self.marks[t]["price"])}
                          for t, p in self.positions.items()},
        }


class PortfolioAgent:
    def __init__(self, portfolio: Portfolio, bus):
        self.portfolio = portfolio
        self.bus = bus
        self.pub = bus.register("portfolio", self.handle, critical=True,
                                subscribes=[FILL, MARK_PRICE, ORDER_CLOSED, RISK_RESET_REQUESTED])

    async def announce(self) -> None:
        self._apply(None, None, 0, lambda: None, "portfolio_invalido")

    def _apply(self, corr, cause, seq: int, mutate, invalid_rule: str) -> None:
        """Aplica a mudança e publica a foto, que carrega `applied_seq`: até que evento já foi aplicada."""
        try:
            mutate()
            view = self.portfolio.view()
            view["applied_seq"] = seq
            self.pub.publish(PORTFOLIO_UPDATED, view, corr, cause)
        except (InvalidFill, KeyError, TypeError) as e:
            self.bus.halt(invalid_rule, INTEGRITY, {"error": repr(e)})
        except PortfolioCorrupt as e:
            self.bus.halt("fill_negativo", INTEGRITY, {"error": str(e)})
        except MarkMissing as e:
            self.bus.halt("mark_ausente", INTEGRITY, {"ticker": str(e)})
        except StateWriteError as e:
            self.bus.halt("estado_portfolio_nao_gravou", INTEGRITY, {"error": str(e)})

    async def handle(self, ev: dict) -> None:
        p, kind = ev["payload"], ev["type"]

        def mutate():
            if kind == FILL:
                self.portfolio.apply_fill(p["ticker"], p["side"], _dec(p["qty"]), _dec(p["price"]))
            elif kind == MARK_PRICE:
                self.portfolio.set_mark(p["ticker"], _dec(p["price"]))
            elif kind == RISK_RESET_REQUESTED:
                self.portfolio.rebase(peak=bool(p.get("peak")), losses=bool(p.get("losses")),
                                      reset_id=p.get("reset_id"))
            # ORDER_CLOSED não muda a carteira: só avança applied_seq, depois dos fills da ordem

        invalid_rule = {FILL: "fill_invalido", MARK_PRICE: "mark_invalida"}.get(kind, "reset_invalido")
        self._apply(ev["correlation_id"], ev["seq"], ev["seq"], mutate, invalid_rule)
