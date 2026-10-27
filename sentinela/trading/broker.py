"""Contrato da corretora e o GuardedBroker, a última porta antes do dinheiro.

O contrato é assíncrono para que o AlpacaBroker (urllib em to_thread, entrega 2) caiba sem mudar o Execution.
Os status da corretora real são traduzidos para este conjunto na entrega 2.
"""
from dataclasses import dataclass
from decimal import Decimal as D
from typing import Protocol

OPEN_STATUSES = frozenset({"open", "partially_filled"})
TERMINAL = frozenset({"filled", "canceled", "rejected", "expired"})


class BrokerError(Exception):
    pass


class BrokerTimeout(BrokerError):
    pass


class DuplicateOrderId(BrokerError):
    """A corretora já tem uma ordem com este client_order_id."""


class GuardRejected(Exception):
    def __init__(self, rule: str, detail: dict | None = None):
        super().__init__(f"{rule}: {detail or {}}")
        self.rule, self.detail = rule, detail or {}


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    ticker: str
    side: str        # "buy" | "sell"
    qty: D
    ref_price: D     # preço de referência do Risk; a ordem é a mercado


@dataclass(frozen=True)
class BrokerOrder:
    client_order_id: str
    broker_order_id: str
    ticker: str
    side: str
    qty: D
    status: str
    filled_qty: D                  # acumulado
    filled_avg_price: D | None     # médio do acumulado


class Broker(Protocol):
    async def submit(self, req: OrderRequest) -> BrokerOrder: ...
    async def get_order(self, client_order_id: str) -> BrokerOrder | None: ...
    async def positions(self) -> dict[str, D]: ...
    async def open_orders(self) -> list[BrokerOrder]: ...


def _finite_positive(x) -> bool:
    return isinstance(x, D) and x.is_finite() and x > 0


@dataclass(frozen=True)
class GuardConfig:
    """Limites do Guard. Valores próprios: não vêm do Risk, para que um erro lá não passe por aqui."""
    max_order_value: D       # compra: qtd × ref_price
    max_qty: D               # compra: protege de um ref_price errado, que encolhe o valor
    allowed_tickers: frozenset

    def __post_init__(self):
        if not _finite_positive(self.max_order_value) or not _finite_positive(self.max_qty):
            raise ValueError("max_order_value e max_qty precisam ser Decimal finito > 0")
        if not self.allowed_tickers:
            raise ValueError("lista de tickers vazia")


class GuardedBroker:
    """Envolve a corretora e recusa o que passa dos limites próprios. Teto e lista valem só para
    compra: sair de uma posição tem de ser possível (D14 da 1A). Leituras passam direto."""

    def __init__(self, inner: Broker, cfg: GuardConfig):
        self._inner, self.cfg = inner, cfg

    async def submit(self, req: OrderRequest) -> BrokerOrder:
        # tudo síncrono até a chamada: o Execution conta com isso para ler os halts sem await no meio
        self._check(req)
        return await self._inner.submit(req)

    def _check(self, req: OrderRequest) -> None:
        if req.side not in ("buy", "sell"):
            raise GuardRejected("lado_invalido", {"side": str(req.side)})
        if not _finite_positive(req.qty):
            raise GuardRejected("qtd_invalida", {"qty": str(req.qty)})
        if not _finite_positive(req.ref_price):
            raise GuardRejected("preco_invalido", {"ref_price": str(req.ref_price)})
        if req.side == "sell":
            return
        if req.ticker not in self.cfg.allowed_tickers:
            raise GuardRejected("ticker_fora_da_lista", {"ticker": req.ticker})
        if req.qty > self.cfg.max_qty:
            raise GuardRejected("qtd_acima_do_maximo",
                                {"qty": str(req.qty), "max_qty": str(self.cfg.max_qty)})
        value = req.qty * req.ref_price
        if value > self.cfg.max_order_value:
            raise GuardRejected("teto_de_valor",
                                {"valor": str(value), "teto": str(self.cfg.max_order_value)})

    async def get_order(self, client_order_id: str) -> BrokerOrder | None:
        return await self._inner.get_order(client_order_id)

    async def positions(self) -> dict[str, D]:
        return await self._inner.positions()

    async def open_orders(self) -> list[BrokerOrder]:
        return await self._inner.open_orders()
