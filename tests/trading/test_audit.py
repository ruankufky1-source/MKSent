import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from sentinela.trading.audit import AuditCorrupt, AuditError, AuditLock, AuditLog, explain

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _append(log, type="X", corr="c1", cause=None, **payload):
    return log.append(agent="t", type=type, correlation_id=corr, causation_id=cause, payload=payload)


def _lines(path):
    return path.read_bytes().split(b"\n")[:-1]


def test_record_has_required_fields_in_utf8(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    _append(log, motivo="ação vetada")
    log.close()
    raw = path.read_bytes()
    assert "ação vetada".encode("utf-8") in raw  # UTF-8 de verdade, sem ç
    rec = json.loads(raw.splitlines()[0])
    assert set(rec) == {"seq", "ts", "agent", "type", "correlation_id", "causation_id", "payload"}
    assert rec["seq"] == 1
    assert rec["ts"].endswith("+00:00")  # UTC com fuso
    assert rec["agent"] == "t" and rec["payload"] == {"motivo": "ação vetada"}


def test_seq_is_contiguous_and_resumes_after_reopen(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    assert [_append(log)["seq"] for _ in range(3)] == [1, 2, 3]
    log.close()
    log = AuditLog(path, clock)
    assert _append(log)["seq"] == 4
    log.close()


def test_gap_in_seq_on_open_is_corrupt(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    for _ in range(3):
        _append(log)
    log.close()
    lines = _lines(path)
    path.write_bytes(lines[0] + b"\n" + lines[2] + b"\n")  # apagou a do meio
    with pytest.raises(AuditCorrupt):
        AuditLog(path, clock)


def test_duplicate_seq_on_open_is_corrupt(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    for _ in range(2):
        _append(log)
    log.close()
    lines = _lines(path)
    path.write_bytes(lines[0] + b"\n" + lines[0] + b"\n" + lines[1] + b"\n")
    with pytest.raises(AuditCorrupt):
        AuditLog(path, clock)


def test_truncated_last_line_is_trimmed_and_recorded(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    for _ in range(2):
        _append(log)
    log.close()
    with open(path, "ab") as f:
        f.write(b'{"seq":3,"ts":"2026-07-20T15:00')  # queda no meio da escrita
    log = AuditLog(path, clock)
    assert log.recovered_tail is True
    rec = _append(log)
    assert rec["seq"] == 4  # 3 foi o registro do aparo
    log.close()
    recs = [json.loads(line) for line in _lines(path)]
    assert [r["seq"] for r in recs] == [1, 2, 3, 4]
    assert recs[2]["type"] == "AuditTailTrimmed"
    assert recs[2]["payload"]["bytes_trimmed"] == len(b'{"seq":3,"ts":"2026-07-20T15:00')


def test_intact_log_is_not_trimmed(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    _append(log)
    log.close()
    log = AuditLog(path, clock)
    assert log.recovered_tail is False
    log.close()
    assert len(_lines(path)) == 1


def test_invalid_complete_line_is_corrupt_not_trimmed(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    _append(log)
    log.close()
    with open(path, "ab") as f:
        f.write(b"isto nao e json\n")
    with pytest.raises(AuditCorrupt):
        AuditLog(path, clock)


def test_second_instance_is_refused_then_allowed_after_close(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    with pytest.raises(AuditLock):
        AuditLog(path, clock)
    log.close()
    AuditLog(path, clock).close()


def test_restart_after_sudden_death_is_accepted(tmp_path):
    path = tmp_path / "audit.jsonl"
    code = (
        "import os, sys\n"
        "from datetime import date\n"
        "from sentinela.trading.clock import SimClock\n"
        "from sentinela.trading.audit import AuditLog\n"
        "log = AuditLog(sys.argv[1], SimClock(date(2026, 7, 20)))\n"
        "log.append(agent='t', type='X', correlation_id='c', causation_id=None, payload={})\n"
        "os._exit(0)\n"  # morte súbita: sem close, sem liberar nada
    )
    subprocess.run([sys.executable, "-c", code, str(path)], cwd=PROJECT_ROOT, check=True)
    from datetime import date
    from sentinela.trading.clock import SimClock
    log = AuditLog(path, SimClock(date(2026, 7, 20)))
    assert _append(log)["seq"] == 2
    log.close()


def test_write_error_raises_and_seq_does_not_advance(tmp_path, clock, monkeypatch):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    _append(log)
    real_write = log._write

    def boom(data):
        raise OSError("disco cheio")

    monkeypatch.setattr(log, "_write", boom)
    with pytest.raises(AuditError):
        _append(log)
    monkeypatch.setattr(log, "_write", real_write)
    assert _append(log)["seq"] == 2  # a falha não consumiu seq
    log.close()
    assert len(_lines(path)) == 2


def test_nonfinite_and_decimal_payload_are_written_as_text(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    _append(log, v=float("nan"), w=float("inf"), d=Decimal("1.50"))
    log.close()
    payload = json.loads(path.read_bytes().splitlines()[0])["payload"]
    assert payload == {"v": "nan", "w": "inf", "d": "1.50"}


def test_explain_rebuilds_chain_from_file_only(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock)
    s = _append(log, type="SignalProposed", corr="c1", ticker="AAA")
    _append(log, type="SignalProposed", corr="c2", ticker="BBB")
    v = _append(log, type="OrderVetoed", corr="c1", cause=s["seq"], rule="trades_day", values={"n": 1})
    log.close()
    chain = explain(path, "c1")  # objeto novo: só o arquivo
    assert [r["type"] for r in chain] == ["SignalProposed", "OrderVetoed"]
    assert chain[1]["causation_id"] == s["seq"]
    assert chain[1]["payload"]["rule"] == "trades_day"
    assert chain[1]["seq"] == v["seq"]
    assert explain(path, "inexistente") == []
