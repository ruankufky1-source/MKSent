import asyncio
import copy
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sentinela.trading.audit import AuditError
from sentinela.trading.events import (
    FILL, MARK_PRICE, ORDER_APPROVED, ORDER_CLOSED, ORDER_VETOED, PORTFOLIO_UPDATED,
    RISK_RESET_REQUESTED, SIGNAL_PROPOSED,
)
from sentinela.trading.halts import INTEGRITY, KILL_SWITCH, LIMIT

QUEUE_MAX = 100
HANDLER_TIMEOUT_S = 5.0
HEARTBEAT_LIMIT_S = 5.0
BEATS_PER_LIMIT = 4    # o loop bate 4 vezes por limite; o vigia confere na mesma cadência
DRAIN_POLL_S = 0.001

# Quem pode publicar cada tipo. Só estes tipos existem; qualquer outro é recusado.
ALLOWED_PUBLISHERS = {
    SIGNAL_PROPOSED: {"strategy"},
    MARK_PRICE: {"data"},
    FILL: {"execution"},
    ORDER_CLOSED: {"execution"},
    ORDER_APPROVED: {"risk"},
    ORDER_VETOED: {"risk"},
    RISK_RESET_REQUESTED: {"risk"},
    PORTFOLIO_UPDATED: {"portfolio"},
}


class PublishDenied(Exception):
    pass


class Publisher:
    """Handle de publicação preso ao nome do agente; só o bus entrega handles válidos."""

    def __init__(self, name: str, bus: "EventBus", key: object):
        self.name = name
        self._bus = bus
        self._key = key

    def publish(self, type: str, payload: dict, correlation_id=None, causation_id=None) -> dict:
        return self._bus._publish(self, type, payload, correlation_id, causation_id)


@dataclass
class _Agent:
    name: str
    handler: object
    critical: bool
    subscribes: frozenset
    queue: asyncio.Queue | None = None
    task: asyncio.Task | None = None


@dataclass
class EventBus:
    audit: object
    halts: object
    clock: object
    marker_path: Path
    queue_max: int = QUEUE_MAX
    handler_timeout: float = HANDLER_TIMEOUT_S
    heartbeat_limit: float = HEARTBEAT_LIMIT_S
    _agents: dict = field(default_factory=dict)
    _keys: dict = field(default_factory=dict)
    _pending: int = 0
    _last_beat: float = 0.0
    _watch_error: str | None = None

    def register(self, name: str, handler=None, subscribes=(), critical: bool = False) -> Publisher:
        if name in self._agents:  # um segundo registro trocaria o handle válido do primeiro
            raise ValueError(f"agente já registrado: {name}")
        agent = _Agent(name, handler, critical, frozenset(subscribes))
        if handler is not None:
            agent.queue = asyncio.Queue(maxsize=self.queue_max)
        self._agents[name] = agent
        key = object()
        self._keys[name] = key
        return Publisher(name, self, key)

    # --- publicação -------------------------------------------------------------------------

    def halt(self, rule: str, klass: str, detail: dict) -> None:
        self.halts.raise_safe(rule, klass, detail)

    def _deny(self, agent: str, type_: str, claimed: str) -> None:
        try:
            self.audit.append(agent=agent, type="PublishDenied", correlation_id="publish",
                              causation_id=None,
                              payload={"attempted_type": type_, "claimed": claimed})
        except AuditError:
            pass
        raise PublishDenied(f"{claimed} não pode publicar {type_}")

    def _publish(self, pub: Publisher, type_: str, payload: dict, corr, cause) -> dict:
        if self._keys.get(pub.name) is not pub._key:
            self._deny("unknown", type_, pub.name)
        if pub.name not in ALLOWED_PUBLISHERS.get(type_, ()):
            self._deny(pub.name, type_, pub.name)
        try:
            rec = self.audit.append(agent=pub.name, type=type_,
                                    correlation_id=corr or uuid.uuid4().hex,
                                    causation_id=cause, payload=payload)
        except AuditError as e:
            self.halt("log_falhou", INTEGRITY, {"error": str(e)})
            raise
        for agent in self._agents.values():
            if agent.queue is None or type_ not in agent.subscribes:
                continue
            try:
                agent.queue.put_nowait(copy.deepcopy(rec))
                self._pending += 1
            except asyncio.QueueFull:
                self.halt("fila_cheia", INTEGRITY, {"agent": agent.name})
        return rec

    def kill(self, who: str, reason: str) -> None:
        self.halt(KILL_SWITCH, LIMIT, {"who": who, "reason": reason})

    # --- execução ---------------------------------------------------------------------------

    def _fail(self, agent: _Agent, ev: dict, exc: BaseException, timed_out: bool) -> None:
        try:
            self.audit.append(agent="bus", type="AgentFailed", correlation_id=ev["correlation_id"],
                              causation_id=ev["seq"],
                              payload={"agent": agent.name, "error": f"{type(exc).__name__}: {exc}"})
        except AuditError as e:
            self.halt("log_falhou", INTEGRITY, {"error": str(e)})
        if timed_out:
            self.halt("handler_lento", INTEGRITY, {"agent": agent.name})
        elif agent.critical:
            self.halt(f"agente_{agent.name}_caiu", INTEGRITY, {"error": str(exc)})

    async def _work(self, agent: _Agent) -> None:
        while True:
            ev = await agent.queue.get()
            try:
                await asyncio.wait_for(agent.handler(ev), self.handler_timeout)
            except asyncio.TimeoutError as e:
                self._fail(agent, ev, e, timed_out=True)
            except Exception as e:  # noqa: BLE001 - o bus existe para isolar agente
                self._fail(agent, ev, e, timed_out=False)
            finally:
                agent.queue.task_done()
                self._pending -= 1

    async def _beat(self) -> None:
        while True:
            self._last_beat = time.monotonic()
            if self._watch_error is not None:  # o vigia não consegue gravar o marcador: ele mesmo é a falha
                self.halt("vigia_falhou", INTEGRITY, {"error": self._watch_error})
                self._watch_error = None
            if self.marker_path.exists():
                self.halt("loop_travado", INTEGRITY, {"limite_s": self.heartbeat_limit})
                try:
                    self.marker_path.unlink()
                except OSError:
                    pass
            await asyncio.sleep(self.heartbeat_limit / BEATS_PER_LIMIT)

    def _watch(self) -> None:
        # Thread à parte: grava só o próprio marcador, nunca o arquivo de estado.
        while not self._stop.wait(self.heartbeat_limit / BEATS_PER_LIMIT):
            if time.monotonic() - self._last_beat > self.heartbeat_limit:
                try:
                    self.marker_path.write_text("loop travado", encoding="utf-8")
                except OSError as e:
                    self._watch_error = str(e)  # quem registra é o loop, quando destravar

    async def start(self) -> None:
        self._last_beat = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        self._beat_task = asyncio.create_task(self._beat())
        for agent in self._agents.values():
            if agent.queue is not None:
                agent.task = asyncio.create_task(self._work(agent))

    async def drain(self) -> None:
        while self._pending:
            await asyncio.sleep(DRAIN_POLL_S)

    async def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        tasks = [self._beat_task] + [a.task for a in self._agents.values() if a.task]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
