"""Execution: o único agente que fala com a corretora, e só pelo GuardedBroker.

Garantias: nada é enviado duas vezes (id determinístico, estado em arquivo, envio ambíguo resolvido por
consulta e nunca reenviado); nada é enviado sem a intenção gravada antes; qualquer dúvida sobre o que a
corretora tem vira halt de integridade. O log é a fonte única do que já foi publicado (Fills e fechamento).
"""
import asyncio
from datetime import datetime
from decimal import Decimal as D, InvalidOperation
from pathlib import Path

from sentinela.trading import store
from sentinela.trading.audit import read_log
from sentinela.trading.broker import (
    BrokerError, BrokerOrder, DuplicateOrderId, GuardedBroker, GuardRejected, OPEN_STATUSES,
    OrderRequest, TERMINAL,
)
from sentinela.trading.events import (
    FILL, HALT_CLEAR_REFUSED, ORDER_APPROVED, ORDER_CLOSED, ORDER_EVENT_IGNORED, ORDER_INTENT,
    ORDER_SUBMITTED, POLL_FAILED, RECONCILED,
)
from sentinela.trading.halts import INTEGRITY, KILL_SWITCH, LIMIT
from sentinela.trading.store import StateCorrupt, StateWriteError, require_absolute

APPROVAL_TTL_S = 30
BROKER_TIMEOUT_S = 1.5   # envio + consulta = 3 s, dentro dos 5 s do handler do bus
POLL_S = 1.0
ID_PREFIX = "sm"
LOG_PAYLOAD_CHARS = 200   # quanto de um payload ilegível vai para o log

INTENT, OPEN, CLOSED, AMBIGUOUS, NOT_SENT, INVALID = (
    "intent", "open", "closed", "ambiguous", "not_sent", "invalid")
_LIVE = (INTENT, AMBIGUOUS, OPEN, INVALID)   # estados que a reconciliação ainda precisa resolver

_BROKER_ERRORS = (TimeoutError, BrokerError, OSError)   # erro de envio que não prova nada


class ReconciliationFailed(Exception):
    def __init__(self, divergences: list):
        super().__init__(f"{len(divergences)} divergência(s) com a corretora")
        self.divergences = divergences


def client_order_id(approved_seq: int, ticker: str, side: str) -> str:
    return f"{ID_PREFIX}-{approved_seq}-{ticker}-{side}"


def _describe(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def _dec(x) -> D | None:
    try:
        return D(str(x))
    except (InvalidOperation, ValueError):
        return None


class Execution:
    def __init__(self, bus, guard, state_path, clock, positions, kill_path, first_run: bool = False,
                 broker_timeout: float = BROKER_TIMEOUT_S, approval_ttl: float = APPROVAL_TTL_S):
        if not isinstance(guard, GuardedBroker):
            raise TypeError("o Execution só aceita um GuardedBroker")
        self.bus, self.guard, self.clock = bus, guard, clock
        self.halts = bus.halts
        self.path = require_absolute(state_path)
        self.kill_path = Path(kill_path)
        self._positions = positions   # função só de leitura: posições da carteira, ticker -> qtd
        self.broker_timeout, self.approval_ttl = broker_timeout, approval_ttl
        self.reconciled = False
        self._frozen = False
        self.orders: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._published: dict[str, tuple] = {}   # approval_id -> (qtd, valor) já publicados nos Fills
        self._closed_in_log: set = set()
        self._load(first_run)
        self._scan_log()
        self.pub = bus.register("execution", self.handle, critical=True, subscribes=[ORDER_APPROVED])

    # --- estado ---------------------------------------------------------------------------

    def _load(self, first_run: bool) -> None:
        if not self.path.exists():
            if first_run:
                self._save()
            else:
                self.halts.raise_safe("execution_estado_ausente", INTEGRITY)
            return
        try:
            self.orders = store.read_json(self.path)["orders"]
        except (StateCorrupt, KeyError, TypeError):
            self.halts.raise_safe("execution_estado_corrompido", INTEGRITY)
            self._frozen = True

    def _save(self) -> None:
        if self._frozen:   # como o HaltStore da 1A: o arquivo estragado fica como está, para ser visto
            raise StateWriteError("estado corrompido: não regravo por cima")
        store.atomic_write_json(self.path, {"orders": self.orders})

    def _persist(self) -> bool:
        try:
            self._save()
            return True
        except StateWriteError as e:
            self.bus.halt("execution_estado_nao_gravou", INTEGRITY, {"error": str(e)})
            return False

    def _scan_log(self) -> None:
        """O que já foi publicado vem do log, não do arquivo de estado: sobrevive à queda entre os dois."""
        for r in read_log(self.bus.audit.path):
            p = r["payload"]
            if r["type"] == FILL:
                q, v = self._published.get(p["approval_id"], (D(0), D(0)))
                self._published[p["approval_id"]] = (q + D(p["qty"]), v + D(p["qty"]) * D(p["price"]))
            elif r["type"] == ORDER_CLOSED:
                self._closed_in_log.add(p["approval_id"])

    def _lock(self, cid: str) -> asyncio.Lock:
        return self._locks.setdefault(cid, asyncio.Lock())

    # --- aprovação ------------------------------------------------------------------------

    def _close(self, rec: dict, status: str, filled: D, reason: str | None = None) -> None:
        payload = {"approval_id": rec["approval_id"], "status": status, "filled_qty": str(filled)}
        if reason:
            payload["reason"] = reason
        self.pub.publish(ORDER_CLOSED, payload, rec["correlation_id"], rec["approved_seq"])
        self._closed_in_log.add(rec["approval_id"])

    def _not_sent(self, rec: dict, reason: str, status: str = NOT_SENT) -> None:
        """Ordem que não saiu: o Risk solta reserva e trade pelo OrderClosed (filled 0)."""
        if rec["approval_id"] not in self._closed_in_log:
            self._close(rec, status, D(0), reason)
        rec["state"], rec["reason"] = NOT_SENT, reason
        self._persist()

    def _record(self, ev: dict, p: dict, cid: str) -> dict:
        return {"approval_id": str(p.get("approval_id")), "ticker": p.get("ticker"),
                "side": p.get("side"), "qty": str(p.get("qtd")), "ref_price": str(p.get("ref_price")),
                "correlation_id": ev["correlation_id"], "approved_seq": ev["seq"],
                "state": INTENT, "broker_order_id": None, "reason": None}

    async def handle(self, ev: dict) -> None:
        p = ev["payload"]
        p = p if isinstance(p, dict) else {}
        side, ticker = p.get("side"), p.get("ticker")
        qty, price = _dec(p.get("qtd")), _dec(p.get("ref_price"))
        valid = side in ("buy", "sell") and isinstance(ticker, str) and qty is not None \
            and price is not None and p.get("approval_id") is not None
        cid = client_order_id(ev["seq"], ticker, side) if valid else f"{ID_PREFIX}-{ev['seq']}-invalida"
        async with self._lock(cid):
            if cid in self.orders:
                self.pub.publish(ORDER_EVENT_IGNORED, {
                    "event": ORDER_APPROVED, "approval_id": str(p.get("approval_id")),
                    "reason": "duplicada"}, ev["correlation_id"], ev["seq"])
                return
            rec = self.orders[cid] = self._record(ev, p, cid)
            if not valid:
                self.bus.halt("aprovacao_invalida", INTEGRITY, {"payload": str(p)[:LOG_PAYLOAD_CHARS]})
                self._not_sent(rec, "aprovacao_invalida")
                return
            if not self.reconciled:
                self._not_sent(rec, "sem_reconciliacao")
                return
            age = (self.clock.now() - datetime.fromisoformat(ev["ts"])).total_seconds()
            if not 0 <= age <= self.approval_ttl:
                self._not_sent(rec, "aprovacao_vencida", status="expired")
                return
            # intenção no log e no arquivo, antes de qualquer contato com a corretora
            self.pub.publish(ORDER_INTENT, {
                "client_order_id": cid, "approval_id": rec["approval_id"], "ticker": ticker,
                "side": side, "qtd": str(qty), "ref_price": str(price)}, ev["correlation_id"], ev["seq"])
            self._persist()   # se falhar, o halt de integridade que isso levanta é lido logo abaixo
            # halts lidos agora, sem nenhum await entre aqui e a chamada da corretora
            if self.kill_path.exists():
                self.bus.halt(KILL_SWITCH, LIMIT, {"arquivo": self.kill_path.name})
            blocked = None
            if self.halts.integrity():
                blocked = "halt_integridade"
            elif side == "buy" and self.halts.limit():
                blocked = f"halt_limite:{self.halts.limit()[0].rule}"
            if blocked:
                self._not_sent(rec, blocked)
                return
            await self._send(cid, rec, OrderRequest(cid, ticker, side, qty, price))

    async def _send(self, cid: str, rec: dict, req: OrderRequest) -> None:
        outcome = "accepted"
        try:
            async with asyncio.timeout(self.broker_timeout):
                order = await self.guard.submit(req)
        except GuardRejected as e:
            self.bus.halt("guard_recusou", INTEGRITY, {"rule": e.rule, **e.detail})
            self._not_sent(rec, f"guard:{e.rule}")
            return
        except DuplicateOrderId:
            outcome, order = "duplicate_adopted", await self._resolve(cid, rec)
        except _BROKER_ERRORS:
            outcome, order = "found_after_timeout", await self._resolve(cid, rec)
        if order is not None:
            self._adopt(cid, rec, order, outcome)

    async def _resolve(self, cid: str, rec: dict) -> BrokerOrder | None:
        """Envio sem resposta confiável: pergunta à corretora pelo id. Nunca reenvia."""
        rec["state"] = AMBIGUOUS
        self._persist()
        try:
            async with asyncio.timeout(self.broker_timeout):
                order = await self.guard.get_order(cid)
        except _BROKER_ERRORS:
            order = None
        if order is None:
            self.bus.halt("envio_ambiguo", INTEGRITY, {"client_order_id": cid})
        return order

    def _adopt(self, cid: str, rec: dict, order: BrokerOrder, outcome: str) -> None:
        rec["state"], rec["broker_order_id"] = OPEN, order.broker_order_id
        self.pub.publish(ORDER_SUBMITTED, {
            "client_order_id": cid, "approval_id": rec["approval_id"], "outcome": outcome,
            "broker_order_id": order.broker_order_id, "status": order.status},
            rec["correlation_id"], rec["approved_seq"])
        self._persist()
        self._apply(cid, rec, order)

    # --- fills e fechamento ---------------------------------------------------------------

    def _invalidate(self, cid: str, rec: dict, reason: str) -> None:
        rec["state"], rec["reason"] = INVALID, reason
        self._persist()
        self.bus.halt("broker_invalido", INTEGRITY, {"client_order_id": cid, "motivo": reason})

    def _check(self, rec: dict, order: BrokerOrder, published: D) -> str | None:
        qty = D(rec["qty"])
        if (order.ticker, order.side, order.qty) != (rec["ticker"], rec["side"], qty):
            return "ordem_diferente"
        if order.status not in OPEN_STATUSES | TERMINAL:
            return "status_desconhecido"
        f = order.filled_qty
        if not (isinstance(f, D) and f.is_finite()) or f > qty:
            return "filled_fora_da_faixa"
        if f < published:
            return "acumulado_diminuiu"
        if f > 0 and not (isinstance(order.filled_avg_price, D) and order.filled_avg_price.is_finite()
                          and order.filled_avg_price > 0):
            return "sem_preco_medio"
        if order.status == "filled" and f != qty:
            return "filled_incompleto"
        return None

    def _apply(self, cid: str, rec: dict, order: BrokerOrder) -> None:
        """Publica o que a corretora tem a mais que o log (Fills) e, se a ordem terminou, o fechamento
        por último. Síncrono: quem chama segura a trava da ordem enquanto consulta."""
        aid = rec["approval_id"]
        pub_q, pub_v = self._published.get(aid, (D(0), D(0)))
        bad = self._check(rec, order, pub_q)
        delta = order.filled_qty - pub_q if bad is None else D(0)
        if bad is None and delta > 0:
            price = (order.filled_qty * order.filled_avg_price - pub_v) / delta
            if not price.is_finite() or price <= 0:
                bad = "preco_do_delta"
        if bad is not None:
            self._invalidate(cid, rec, bad)
            return
        rec["state"] = OPEN
        if delta > 0:
            self.pub.publish(FILL, {"approval_id": aid, "ticker": rec["ticker"], "side": rec["side"],
                                    "qty": str(delta), "price": str(price)},
                             rec["correlation_id"], rec["approved_seq"])
            self._published[aid] = (order.filled_qty, pub_v + delta * price)
        if order.status in TERMINAL:
            if aid not in self._closed_in_log:
                self._close(rec, order.status, order.filled_qty)
            rec["state"] = CLOSED
            self._persist()

    async def _lookup(self, cid: str) -> BrokerOrder | None:
        async with asyncio.timeout(self.broker_timeout):
            return await self.guard.get_order(cid)

    async def poll_once(self) -> None:
        """Consulta as ordens abertas e publica o que mudou. Segue com halt ativo: o dinheiro já saiu."""
        for cid in [c for c, r in self.orders.items() if r["state"] == OPEN]:
            async with self._lock(cid):
                rec = self.orders[cid]
                try:
                    order = await self._lookup(cid)
                except _BROKER_ERRORS as e:
                    order, why = None, _describe(e)
                else:
                    why = "ordem_nao_encontrada"
                if order is None:
                    self.pub.publish(POLL_FAILED, {"client_order_id": cid, "error": why},
                                     rec["correlation_id"], rec["approved_seq"])
                    continue
                self._apply(cid, rec, order)

    async def run_poller(self, interval: float = POLL_S) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception as e:  # noqa: BLE001 - agente crítico: a queda vira halt
                self.bus.halt("agente_execution_caiu", INTEGRITY, {"error": _describe(e)})
                return
            await asyncio.sleep(interval)

    # --- reconciliação --------------------------------------------------------------------

    async def reconcile(self, *, halt: bool = True) -> list[dict]:
        """Confere a corretora com o que o sistema sabe. Só roda fora de um handler do bus (espera o
        bus esvaziar). Passos: (1) consulta cada ordem pendente; (2) publica o que falta; (3) fecha
        aprovações do log sem estado; (4) espera a carteira aplicar; (5) compara posições e ordens
        abertas. Divergência = halt de integridade (se `halt`)."""
        divs: list[dict] = []
        for cid in [c for c, r in self.orders.items() if r["state"] in _LIVE]:
            async with self._lock(cid):
                rec = self.orders[cid]
                if rec["state"] not in _LIVE:
                    continue
                try:
                    order = await self._lookup(cid)
                except _BROKER_ERRORS as e:
                    divs.append({"kind": "corretora_indisponivel", "error": str(e)})
                    break
                if order is None:
                    if rec["state"] in (OPEN, INVALID):
                        divs.append({"kind": "ordem_sumiu", "client_order_id": cid})
                    else:   # intenção ou envio ambíguo que a corretora nunca recebeu
                        self._not_sent(rec, "nao_encontrada_na_reconciliacao")
                    continue
                if rec["state"] in (INTENT, AMBIGUOUS):
                    self._adopt(cid, rec, order, "adopted_on_reconcile")
                else:
                    self._apply(cid, rec, order)
                if rec["state"] == INVALID:
                    divs.append({"kind": "ordem_invalida", "client_order_id": cid,
                                 "motivo": rec["reason"]})
        self._close_approvals_without_state()
        await self.bus.drain()
        divs += await self._compare()
        self.pub.publish(RECONCILED, {"divergences": divs}, correlation_id="reconcile")
        if divs and halt:
            self.bus.halt("reconciliacao_divergente", INTEGRITY, {"divergencias": divs})
        if not divs:
            self.reconciled = True
            if not self.path.exists():   # estado ausente: sem isto o halt voltaria a cada reinício
                self._persist()
        return divs

    def _close_approvals_without_state(self) -> None:
        """Aprovação no log sem estado e sem fechamento: a fila em memória se perdeu. Nunca é enviada
        depois; fecha para o Risk soltar a reserva (ele decide de novo no próximo sinal)."""
        for r in read_log(self.bus.audit.path):
            p = r["payload"]
            if r["type"] != ORDER_APPROVED or p.get("approval_id") in self._closed_in_log:
                continue
            cid = client_order_id(r["seq"], p.get("ticker"), p.get("side"))
            if cid in self.orders:
                continue
            rec = self.orders[cid] = self._record(r, p, cid)
            self._not_sent(rec, "reinicio")

    async def _compare(self) -> list[dict]:
        try:
            async with asyncio.timeout(self.broker_timeout):
                broker_pos = await self.guard.positions()
            async with asyncio.timeout(self.broker_timeout):
                broker_open = await self.guard.open_orders()
        except _BROKER_ERRORS as e:
            return [{"kind": "corretora_indisponivel", "error": str(e)}]
        mine = self._positions()
        divs = []
        for t in sorted(set(broker_pos) | set(mine)):
            b, m = broker_pos.get(t, D(0)), mine.get(t, D(0))
            if b != m:
                divs.append({"kind": "posicao", "ticker": t, "broker": str(b), "portfolio": str(m)})
        for o in broker_open:
            rec = self.orders.get(o.client_order_id)
            if rec is None:
                divs.append({"kind": "ordem_desconhecida", "client_order_id": o.client_order_id})
            elif rec["state"] in (CLOSED, NOT_SENT):
                divs.append({"kind": "ordem_reaberta", "client_order_id": o.client_order_id})
        return divs

    async def clear_halt(self, risk_agent, who: str, reason: str):
        """Libera as travas. Trava de integridade só sai depois de uma reconciliação limpa. Só pode ser
        chamado fora de um handler do bus (a reconciliação espera o bus esvaziar)."""
        if not self.halts.integrity():
            return risk_agent.clear_halt(who, reason)
        divs = await self.reconcile(halt=False)
        if divs:
            self.pub.publish(HALT_CLEAR_REFUSED, {"who": who, "reason": reason, "divergences": divs},
                             correlation_id="halt:all")
            raise ReconciliationFailed(divs)
        return risk_agent.clear_halt(who, reason, reconciled=True)
