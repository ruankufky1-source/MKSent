import asyncio
import json
import time

import pytest

from sentinela.trading.audit import AuditError, AuditLog, read_log
from sentinela.trading.bus import EventBus, Publisher, PublishDenied
from sentinela.trading.halts import HaltStore


def make(tmp_path, clock, **kw):
    audit = AuditLog(tmp_path / "audit.jsonl", clock)
    halts = HaltStore(tmp_path / "halts.json", clock, audit=audit, first_run=True)
    bus = EventBus(audit, halts, clock, marker_path=tmp_path / "LOOP_TRAVADO", **kw)
    return bus, audit, halts


def types(tmp_path):
    return [r["type"] for r in read_log(tmp_path / "audit.jsonl")]


def run(coro):
    return asyncio.run(coro)


def test_handler_finds_its_own_event_already_on_disk(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock)
    seen = []

    async def handler(ev):
        on_disk = [r["seq"] for r in read_log(tmp_path / "audit.jsonl")]
        seen.append(ev["seq"] in on_disk)

    bus.register("portfolio", handler, subscribes=["MarkPrice"])
    data = bus.register("data")

    async def main():
        await bus.start()
        data.publish("MarkPrice", {"ticker": "AAA", "price": "10"})
        await bus.drain()
        await bus.stop()

    run(main())
    assert seen == [True]


def test_only_risk_handle_can_publish_order_approved(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock)
    got = []

    async def handler(ev):
        got.append((ev["agent"], ev["type"]))

    bus.register("execution", handler, subscribes=["OrderApproved"])
    strategy = bus.register("strategy")
    risk = bus.register("risk")

    async def main():
        await bus.start()
        with pytest.raises(PublishDenied):
            strategy.publish("OrderApproved", {"qtd": "1"})  # agente errado
        risk.publish("OrderApproved", {"qtd": "1"})  # controle positivo
        await bus.drain()
        await bus.stop()

    run(main())
    assert got == [("risk", "OrderApproved")]  # o falso não foi entregue
    denied = [r for r in read_log(tmp_path / "audit.jsonl") if r["type"] == "PublishDenied"]
    assert len(denied) == 1 and denied[0]["agent"] == "strategy"
    assert denied[0]["payload"]["attempted_type"] == "OrderApproved"


def test_forged_risk_handle_is_denied_and_logged(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock)
    bus.register("risk")
    forged = Publisher("risk", bus, key=object())  # código que só "se declara" Risk

    with pytest.raises(PublishDenied):
        forged.publish("OrderApproved", {"qtd": "1"})
    denied = [r for r in read_log(tmp_path / "audit.jsonl") if r["type"] == "PublishDenied"]
    assert len(denied) == 1 and denied[0]["payload"]["claimed"] == "risk"


def test_unknown_event_type_is_denied(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock)
    strategy = bus.register("strategy")
    with pytest.raises(PublishDenied):
        strategy.publish("TipoInventado", {})


def test_subscribers_get_independent_copies(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock)
    seen = []

    async def mutator(ev):
        ev["payload"]["ticker"] = "ZZZ"  # mexe na própria cópia

    async def observer(ev):
        seen.append(ev["payload"]["ticker"])

    bus.register("portfolio", mutator, subscribes=["MarkPrice"])
    bus.register("risk", observer, subscribes=["MarkPrice"])
    data = bus.register("data")

    async def main():
        await bus.start()
        data.publish("MarkPrice", {"ticker": "AAA", "price": "10"})
        await bus.drain()
        await bus.stop()

    run(main())
    assert seen == ["AAA"]


def test_noncritical_exception_is_logged_and_others_continue(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock)
    got = []

    async def bad(ev):
        raise RuntimeError("estratégia quebrou")

    async def good(ev):
        got.append(ev["seq"])

    bus.register("strategy", bad, subscribes=["MarkPrice"])
    bus.register("execution", good, subscribes=["MarkPrice"])
    data = bus.register("data")

    async def main():
        await bus.start()
        data.publish("MarkPrice", {"ticker": "A", "price": "1"})
        data.publish("MarkPrice", {"ticker": "A", "price": "2"})
        await bus.drain()
        await bus.stop()

    run(main())
    assert len(got) == 2  # o outro agente seguiu
    assert types(tmp_path).count("AgentFailed") == 2
    assert halts.active() == []  # agente não crítico não derruba o sistema


@pytest.mark.parametrize("name", ["risk", "portfolio", "execution"])
def test_critical_agent_failure_is_integrity_halt(tmp_path, clock, name):
    bus, audit, halts = make(tmp_path, clock)

    async def bad(ev):
        raise RuntimeError("caiu")

    bus.register(name, bad, subscribes=["MarkPrice"], critical=True)
    data = bus.register("data")

    async def main():
        await bus.start()
        data.publish("MarkPrice", {"ticker": "A", "price": "1"})
        await bus.drain()
        await bus.stop()

    run(main())
    assert [r.rule for r in halts.active()] == [f"agente_{name}_caiu"]
    assert halts.integrity()


def test_queue_boundary_third_passes_fourth_halts(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock, queue_max=3)

    async def handler(ev):
        pass

    bus.register("portfolio", handler, subscribes=["MarkPrice"])
    data = bus.register("data")
    for _ in range(3):  # sem await: o consumidor ainda não rodou
        data.publish("MarkPrice", {"ticker": "A", "price": "1"})
    assert halts.active() == []
    data.publish("MarkPrice", {"ticker": "A", "price": "1"})
    assert [r.rule for r in halts.active()] == ["fila_cheia"]


def test_slow_handler_is_integrity_halt_fast_one_is_not(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock, handler_timeout=0.05)

    async def fast(ev):
        pass

    async def slow(ev):
        await asyncio.sleep(1)

    bus.register("portfolio", fast, subscribes=["MarkPrice"])
    bus.register("strategy", slow, subscribes=["Fill"])
    data = bus.register("data")
    ex = bus.register("execution")

    async def main():
        await bus.start()
        data.publish("MarkPrice", {"ticker": "A", "price": "1"})
        await bus.drain()
        assert halts.active() == []  # controle positivo
        ex.publish("Fill", {"x": 1})
        await bus.drain()
        await bus.stop()

    run(main())
    assert [r.rule for r in halts.active()] == ["handler_lento"]


def test_blocking_handler_is_caught_by_heartbeat_watchdog(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock, heartbeat_limit=0.2, handler_timeout=30)
    marker = tmp_path / "LOOP_TRAVADO"
    marker_seen_by_handler = []

    async def blocking(ev):
        time.sleep(0.7)  # trava o loop inteiro; o timeout do asyncio não pega isto
        marker_seen_by_handler.append(marker.exists())  # a thread gravou o próprio marcador

    bus.register("portfolio", blocking, subscribes=["MarkPrice"])
    data = bus.register("data")

    async def main():
        await bus.start()
        data.publish("MarkPrice", {"ticker": "A", "price": "1"})
        await bus.drain()
        await asyncio.sleep(0.5)  # o loop destrava e lê o marcador
        await bus.stop()

    run(main())
    assert marker_seen_by_handler == [True]
    assert [r.rule for r in halts.active()] == ["loop_travado"]
    assert not marker.exists()  # consumido pelo loop


def test_healthy_loop_does_not_trip_watchdog(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock, heartbeat_limit=0.2)

    async def main():
        await bus.start()
        await asyncio.sleep(1.5)  # várias vezes o limite
        await bus.stop()

    run(main())
    assert halts.active() == []


def test_loop_beats_at_least_twice_per_limit():
    # com menos de 2 batimentos por limite o vigia dispara em loop saudável
    from sentinela.trading.bus import BEATS_PER_LIMIT
    assert BEATS_PER_LIMIT >= 2


def test_audit_write_failure_blocks_delivery_and_halts(tmp_path, clock, monkeypatch):
    bus, audit, halts = make(tmp_path, clock)
    got = []

    async def handler(ev):
        got.append(ev)

    bus.register("portfolio", handler, subscribes=["MarkPrice"])
    data = bus.register("data")

    def boom(raw):
        raise OSError("disco cheio")

    async def main():
        await bus.start()
        monkeypatch.setattr(audit, "_write", boom)
        with pytest.raises(AuditError):
            data.publish("MarkPrice", {"ticker": "A", "price": "1"})
        await bus.drain()
        await bus.stop()

    run(main())
    assert got == []  # ninguém recebeu um evento que não foi gravado
    assert "log_falhou" in [r.rule for r in halts.active()]


def test_registering_the_same_name_twice_is_refused_and_keeps_the_first_handle(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock)
    first = bus.register("risk")
    with pytest.raises(ValueError):
        bus.register("risk")
    # o handle do primeiro continua o único válido (um segundo registro não o substitui)
    assert bus._keys["risk"] is first._key


def test_default_limits_match_the_plan(tmp_path, clock):
    from sentinela.trading import bus as bus_module
    assert (bus_module.QUEUE_MAX, bus_module.HANDLER_TIMEOUT_S, bus_module.HEARTBEAT_LIMIT_S) == (
        100, 5.0, 5.0)
    bus, audit, halts = make(tmp_path, clock)  # sem sobrescrever nada: valem os padrões
    assert (bus.queue_max, bus.handler_timeout, bus.heartbeat_limit) == (100, 5.0, 5.0)

    async def handler(ev):
        pass

    bus.register("portfolio", handler, subscribes=["MarkPrice"])
    data = bus.register("data")
    for _ in range(100):  # sem await: o consumidor ainda não rodou
        data.publish("MarkPrice", {"ticker": "A", "price": "1"})
    assert halts.active() == []
    data.publish("MarkPrice", {"ticker": "A", "price": "1"})  # a 101ª
    assert [r.rule for r in halts.active()] == ["fila_cheia"]


def test_watchdog_that_cannot_write_its_marker_is_itself_a_halt(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock, heartbeat_limit=0.2, handler_timeout=30)
    bus.marker_path = tmp_path / "pasta_que_nao_existe" / "LOOP_TRAVADO"

    async def blocking(ev):
        time.sleep(0.7)

    bus.register("portfolio", blocking, subscribes=["MarkPrice"])
    data = bus.register("data")

    async def main():
        await bus.start()
        data.publish("MarkPrice", {"ticker": "A", "price": "1"})
        await bus.drain()
        await asyncio.sleep(0.5)
        await bus.stop()

    run(main())
    assert [r.rule for r in halts.active()] == ["vigia_falhou"]


def test_halt_that_cannot_be_persisted_stays_in_memory_and_is_logged(tmp_path, clock, monkeypatch):
    from sentinela.trading import store
    from sentinela.trading.store import StateWriteError
    bus, audit, halts = make(tmp_path, clock)

    def boom(path, obj):
        raise StateWriteError("disco cheio")

    monkeypatch.setattr(store, "atomic_write_json", boom)
    bus.halt("fila_cheia", "integrity", {"agent": "x"})
    assert [r.rule for r in halts.active()] == ["fila_cheia"]
    assert "HaltNotPersisted" in types(tmp_path)


def test_kill_raises_limit_halt(tmp_path, clock):
    bus, audit, halts = make(tmp_path, clock)
    bus.kill("ruan", "mercado estranho")
    assert [r.rule for r in halts.active()] == ["kill_switch"]
    assert halts.limit() and not halts.integrity()
