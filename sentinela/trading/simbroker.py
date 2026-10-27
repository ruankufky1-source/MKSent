"""Corretora simulada para os testes do paper: sem rede, com semente fixa e falhas injetáveis."""
import asyncio
import dataclasses
import random
from decimal import Decimal as D

from sentinela.trading.broker import (
    BrokerError, BrokerOrder, BrokerTimeout, DuplicateOrderId, OrderRequest, TERMINAL,
)

PARTIAL_PROB = 0.10
STALL_S = 10.0   # quanto o sim "dorme" numa falha de timeout; o Execution cancela antes disso


class _Sim:
    def __init__(self, req: OrderRequest, chunks, status="open"):
        self.req, self.chunks, self.idx = req, list(chunks), 0
        self.status, self.filled, self.value = status, D(0), D(0)
        self.override: dict = {}


class SimBroker:
    def __init__(self, seed: int = 0, partial_prob: float = PARTIAL_PROB, stall_s: float = STALL_S):
        self._rng = random.Random(seed)
        self.partial_prob, self.stall_s = partial_prob, stall_s
        self.orders: dict[str, _Sim] = {}
        self.submit_calls = 0
        self.read_calls = 0
        self.before_submit = None        # gancho de teste: recebe o OrderRequest
        self._pos: dict[str, D] = {}
        self._faults: list[str] = []
        self._lookup_faults: list[str] = []
        self._plan = None
        self._override_next: dict | None = None
        self._hold: asyncio.Event | None = None

    # --- controles de teste ---------------------------------------------------------------

    def inject(self, *faults: str) -> None:
        """Falhas de envio, uma por chamada de submit, em fila: timeout, lost_response, reject,
        duplicate, connection_error, connection_error_lost."""
        self._faults.extend(faults)

    def inject_lookup(self, *faults: str) -> None:
        self._lookup_faults.extend(faults)   # timeout | error, uma por chamada de get_order

    def plan_next(self, chunks) -> None:
        self._plan = [(D(q), D(p)) for q, p in chunks]

    def override_next(self, **fields) -> None:
        self._override_next = fields

    def override(self, cid: str, **fields) -> None:
        self.orders[cid].override = fields   # sem campos: volta ao normal

    def hold_next_lookup(self) -> asyncio.Event:
        """A próxima consulta tira a foto da ordem e só responde quando o evento for liberado."""
        self._hold = asyncio.Event()
        return self._hold

    def advance(self) -> None:
        for o in self.orders.values():
            if o.status not in TERMINAL:
                self._step(o)

    def cancel(self, cid: str) -> None:
        self._end(cid, "canceled")

    def expire(self, cid: str) -> None:
        self._end(cid, "expired")

    def forget(self, cid: str) -> None:
        del self.orders[cid]

    def set_positions(self, positions: dict) -> None:
        self._pos = {k: D(v) for k, v in positions.items() if D(v) != 0}

    def add_foreign_order(self, cid: str, ticker: str, side: str, qty: D) -> None:
        self.orders[cid] = _Sim(OrderRequest(cid, ticker, side, qty, D(1)), [])

    def create_now(self, req: OrderRequest) -> None:
        self._create(req, reject=False)

    # --- interno --------------------------------------------------------------------------

    def _end(self, cid: str, status: str) -> None:
        o = self.orders[cid]
        if o.status not in TERMINAL:
            o.status = status

    def _default_chunks(self, req: OrderRequest):
        partial = self._rng.random() < self.partial_prob   # sempre sorteia: a sequência não depende do resto
        if partial and req.qty >= 2:
            first = req.qty // 2
            return [(first, req.ref_price), (req.qty - first, req.ref_price)]
        return [(req.qty, req.ref_price)]

    def _create(self, req: OrderRequest, reject: bool) -> _Sim:
        if reject:
            o = _Sim(req, [], status="rejected")
        else:
            chunks, self._plan = (self._plan, None) if self._plan else (self._default_chunks(req), None)
            o = _Sim(req, chunks)
        if self._override_next is not None:
            o.override, self._override_next = self._override_next, None
        self.orders[req.client_order_id] = o
        if not reject:
            self._step(o)
        return o

    def _step(self, o: _Sim) -> None:
        if o.idx >= len(o.chunks):
            return
        qty, price = o.chunks[o.idx]
        o.idx += 1
        o.filled += qty
        o.value += qty * price
        sign = 1 if o.req.side == "buy" else -1
        t = o.req.ticker
        self._pos[t] = self._pos.get(t, D(0)) + sign * qty
        if self._pos[t] == 0:
            del self._pos[t]
        o.status = "filled" if o.idx == len(o.chunks) else "partially_filled"

    def _snap(self, o: _Sim) -> BrokerOrder:
        r = o.req
        snap = BrokerOrder(r.client_order_id, "sim-" + r.client_order_id, r.ticker, r.side, r.qty,
                           o.status, o.filled, o.value / o.filled if o.filled else None)
        return dataclasses.replace(snap, **o.override)

    # --- contrato -------------------------------------------------------------------------

    async def submit(self, req: OrderRequest) -> BrokerOrder:
        self.submit_calls += 1
        if self.before_submit is not None:
            self.before_submit(req)
        fault = self._faults.pop(0) if self._faults else None
        if fault == "timeout":   # a ordem nunca chegou
            await asyncio.sleep(self.stall_s)
            raise BrokerTimeout("sem resposta")
        if req.client_order_id in self.orders:
            raise DuplicateOrderId(req.client_order_id)
        if fault == "connection_error":
            raise ConnectionError("conexão caiu antes de enviar")
        o = self._create(req, reject=(fault == "reject"))
        if fault == "lost_response":   # a ordem chegou, a resposta não
            await asyncio.sleep(self.stall_s)
            raise BrokerTimeout("resposta perdida")
        if fault == "connection_error_lost":
            raise ConnectionError("conexão caiu depois de enviar")
        if fault == "duplicate":
            raise DuplicateOrderId(req.client_order_id)
        return self._snap(o)

    async def get_order(self, client_order_id: str) -> BrokerOrder | None:
        self.read_calls += 1
        fault = self._lookup_faults.pop(0) if self._lookup_faults else None
        if fault == "timeout":
            await asyncio.sleep(self.stall_s)
            raise BrokerTimeout("consulta sem resposta")
        if fault == "error":
            raise BrokerError("consulta falhou")
        o = self.orders.get(client_order_id)
        snap = None if o is None else self._snap(o)
        if self._hold is not None:
            gate, self._hold = self._hold, None
            await gate.wait()
        return snap

    async def positions(self) -> dict[str, D]:
        self.read_calls += 1
        return dict(self._pos)

    async def open_orders(self) -> list[BrokerOrder]:
        self.read_calls += 1
        return [self._snap(o) for o in self.orders.values() if o.status not in TERMINAL]
