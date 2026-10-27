import json
from datetime import date

import pytest

from sentinela.trading import store
from sentinela.trading.audit import AuditError, AuditLog
from sentinela.trading.halts import INTEGRITY, LIMIT, HaltStore
from sentinela.trading.store import StateWriteError


def _rules(h):
    return sorted(r.rule for r in h.active())


def test_first_run_creates_empty_state(tmp_path, clock):
    h = HaltStore(tmp_path / "halts.json", clock, first_run=True)
    assert h.active() == []
    assert (tmp_path / "halts.json").exists()


def test_missing_file_without_first_run_is_integrity_halt(tmp_path, clock):
    h = HaltStore(tmp_path / "halts.json", clock)
    assert _rules(h) == ["estado_ausente"]
    assert h.integrity() and not h.limit()


def test_missing_state_halt_survives_restart(tmp_path, clock):
    HaltStore(tmp_path / "halts.json", clock)
    h = HaltStore(tmp_path / "halts.json", clock)  # segunda partida: arquivo existe, halt continua
    assert _rules(h) == ["estado_ausente"]


def test_corrupt_file_is_integrity_halt(tmp_path, clock):
    path = tmp_path / "halts.json"
    path.write_text("{isto nao e json", encoding="utf-8")
    h = HaltStore(path, clock)
    assert _rules(h) == ["estado_corrompido"]


def test_halt_is_persisted_before_raise_returns(tmp_path, clock):
    path = tmp_path / "halts.json"
    h = HaltStore(path, clock, first_run=True)
    h.raise_("drawdown", LIMIT, {"value": -0.2})
    again = HaltStore(path, clock)  # reinício: continua em halt
    assert _rules(again) == ["drawdown"]


def test_same_rule_twice_is_one_record(tmp_path, clock):
    h = HaltStore(tmp_path / "halts.json", clock, first_run=True)
    assert h.raise_("drawdown", LIMIT) is True
    assert h.raise_("drawdown", LIMIT) is False
    assert _rules(h) == ["drawdown"]


def test_daily_halt_latches_all_day_and_expires_next_day(tmp_path, clock):
    h = HaltStore(tmp_path / "halts.json", clock, first_run=True)
    h.raise_("daily_loss", LIMIT, scope="day")
    assert _rules(h) == ["daily_loss"]  # recuperar no meio do dia não destrava
    clock.set_date(date(2026, 7, 21))
    assert h.active() == []


def test_weekly_halt_survives_the_week_and_expires_next_iso_week(tmp_path, clock):
    clock.set_date(date(2026, 7, 22))  # quarta
    h = HaltStore(tmp_path / "halts.json", clock, first_run=True)
    h.raise_("weekly_loss", LIMIT, scope="week")
    clock.set_date(date(2026, 7, 24))  # sexta, mesma semana
    assert _rules(h) == ["weekly_loss"]
    clock.set_date(date(2026, 7, 27))  # segunda seguinte
    assert h.active() == []


def test_halts_without_scope_never_expire_by_date(tmp_path, clock):
    h = HaltStore(tmp_path / "halts.json", clock, first_run=True)
    h.raise_("drawdown", LIMIT)
    h.raise_("fila_cheia", INTEGRITY)
    clock.set_date(date(2026, 12, 31))
    assert _rules(h) == ["drawdown", "fila_cheia"]


def test_clear_empties_persists_and_is_logged(tmp_path, clock):
    audit = AuditLog(tmp_path / "audit.jsonl", clock)
    h = HaltStore(tmp_path / "halts.json", clock, audit=audit, first_run=True)
    h.raise_("drawdown", LIMIT)
    cleared = h.clear("ruan", "conferi a conta")
    assert [r.rule for r in cleared] == ["drawdown"]
    assert h.active() == []
    assert HaltStore(tmp_path / "halts.json", clock).active() == []
    audit.close()
    types = [json.loads(line)["type"] for line in (tmp_path / "audit.jsonl").read_bytes().splitlines()]
    assert types == ["HaltRaised", "HaltCleared"]
    cleared_rec = json.loads((tmp_path / "audit.jsonl").read_bytes().splitlines()[1])
    assert cleared_rec["payload"]["who"] == "ruan"
    assert cleared_rec["payload"]["reason"] == "conferi a conta"


def test_state_write_failure_raises(tmp_path, clock, monkeypatch):
    h = HaltStore(tmp_path / "halts.json", clock, first_run=True)

    def boom(path, obj):
        raise StateWriteError("disco cheio")

    monkeypatch.setattr(store, "atomic_write_json", boom)
    with pytest.raises(StateWriteError):
        h.raise_("drawdown", LIMIT)
    assert [r.rule for r in h.active()] == ["drawdown"]  # a trava vale na memória mesmo assim


def test_clear_with_a_failing_file_write_keeps_everything_halted(tmp_path, clock, monkeypatch):
    audit = AuditLog(tmp_path / "audit.jsonl", clock)
    h = HaltStore(tmp_path / "halts.json", clock, audit=audit, first_run=True)
    h.raise_("drawdown", LIMIT)

    def boom(path, obj):
        raise StateWriteError("disco cheio")

    monkeypatch.setattr(store, "atomic_write_json", boom)
    with pytest.raises(StateWriteError):
        h.clear("ruan", "conferi")
    assert [r.rule for r in h.active()] == ["drawdown"]            # memória não foi limpa
    assert [r.rule for r in HaltStore(tmp_path / "halts.json", clock).active()] == ["drawdown"]
    audit.close()
    types = [json.loads(line)["type"] for line in (tmp_path / "audit.jsonl").read_bytes().splitlines()]
    assert types == ["HaltRaised", "HaltCleared", "HaltClearFailed"]  # o log não afirma o que não houve


def test_clear_with_a_failing_log_aborts_the_clear(tmp_path, clock, monkeypatch):
    audit = AuditLog(tmp_path / "audit.jsonl", clock)
    h = HaltStore(tmp_path / "halts.json", clock, audit=audit, first_run=True)
    h.raise_("drawdown", LIMIT)

    def boom(raw):
        raise OSError("disco cheio")

    monkeypatch.setattr(audit, "_write", boom)
    with pytest.raises(AuditError):
        h.clear("ruan", "conferi")
    assert [r.rule for r in h.active()] == ["drawdown"]
    assert [r.rule for r in HaltStore(tmp_path / "halts.json", clock).active()] == ["drawdown"]


def test_raise_safe_keeps_the_halt_in_memory_and_logs_that_it_was_not_persisted(
        tmp_path, clock, monkeypatch):
    audit = AuditLog(tmp_path / "audit.jsonl", clock)
    h = HaltStore(tmp_path / "halts.json", clock, audit=audit, first_run=True)

    def boom(path, obj):
        raise StateWriteError("disco cheio")

    monkeypatch.setattr(store, "atomic_write_json", boom)
    assert h.raise_safe("fila_cheia", INTEGRITY, {"agent": "x"}) is True   # não levanta
    assert [r.rule for r in h.active()] == ["fila_cheia"]
    audit.close()
    recs = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_bytes().splitlines()]
    assert [r["type"] for r in recs] == ["HaltRaised", "HaltNotPersisted"]
    assert recs[1]["payload"]["rule"] == "fila_cheia"


def test_missing_state_halt_that_cannot_be_written_is_logged(tmp_path, clock, monkeypatch):
    audit = AuditLog(tmp_path / "audit.jsonl", clock)

    def boom(path, obj):
        raise StateWriteError("disco cheio")

    monkeypatch.setattr(store, "atomic_write_json", boom)
    h = HaltStore(tmp_path / "halts.json", clock, audit=audit)  # arquivo ausente, sem first_run
    assert [r.rule for r in h.active()] == ["estado_ausente"]   # vale na memória
    audit.close()
    recs = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_bytes().splitlines()]
    assert [r["type"] for r in recs] == ["HaltNotPersisted"]
    assert recs[0]["payload"]["rule"] == "estado_ausente"


def test_kill_switch_name_is_a_shared_constant():
    from sentinela.trading.halts import KILL_SWITCH
    assert KILL_SWITCH == "kill_switch"
