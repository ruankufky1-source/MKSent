import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal

from sentinela.trading.store import require_absolute


class AuditError(Exception):
    pass


class AuditCorrupt(AuditError):
    pass


class AuditLock(AuditError):
    pass


def _clean(obj):
    """Deixa o payload serializável sem perder o valor: NaN/inf e Decimal viram texto."""
    if isinstance(obj, float):
        return obj if obj == obj and obj not in (float("inf"), float("-inf")) else repr(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_clean(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return str(obj)


def _canonical(rec: dict) -> bytes:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _parse(data: bytes) -> list[dict]:
    recs = []
    for i, line in enumerate(data.split(b"\n")[:-1], start=1):
        try:
            rec = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise AuditCorrupt(f"linha {i} não é JSON") from e
        if not isinstance(rec, dict) or rec.get("seq") != i:
            raise AuditCorrupt(f"seq fora de ordem na linha {i}: esperado {i}")
        recs.append(rec)
    return recs


class AuditLog:
    """JSONL append-only, uma linha por evento, fsync por linha, seq contíguo, instância única.

    O lock é do sistema operacional: morte súbita do processo o libera sozinho.
    """

    def __init__(self, path, clock):
        self.path = require_absolute(path)
        self.clock = clock
        self.recovered_tail = False
        self._fd = None
        self._seq = 0
        self._size = 0
        self._lockfd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT)
        try:
            _lock(self._lockfd)
        except OSError as e:
            os.close(self._lockfd)
            raise AuditLock(f"outra instância já usa {self.path}") from e
        try:
            trimmed = self._recover()
            self._fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT
                               | getattr(os, "O_BINARY", 0))
            if trimmed:
                self.recovered_tail = True
                self.append(agent="audit", type="AuditTailTrimmed", correlation_id="audit",
                            causation_id=None, payload={"bytes_trimmed": trimmed})
        except BaseException:
            self.close()
            raise

    def _recover(self) -> int:
        if not self.path.exists():
            return 0
        data = self.path.read_bytes()
        trimmed = 0
        if data and not data.endswith(b"\n"):
            cut = data.rfind(b"\n") + 1
            trimmed = len(data) - cut
            with open(self.path, "r+b") as f:
                f.truncate(cut)
                f.flush()
                os.fsync(f.fileno())
            data = data[:cut]
        self._seq = len(_parse(data))
        self._size = len(data)
        return trimmed

    def _write(self, raw: bytes) -> None:
        view = memoryview(raw)
        while view:
            view = view[os.write(self._fd, view):]
        os.fsync(self._fd)

    def append(self, *, agent: str, type: str, correlation_id, causation_id, payload) -> dict:
        rec = {
            "seq": self._seq + 1,
            "ts": self.clock.now().astimezone(timezone.utc).isoformat(),
            "agent": agent,
            "type": type,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "payload": _clean(payload),
        }
        raw = _canonical(rec) + b"\n"
        try:
            self._write(raw)
        except OSError as e:
            try:  # não deixar meia linha para trás
                os.ftruncate(self._fd, self._size)
            except OSError:
                pass
            raise AuditError(f"falha ao gravar o log: {e}") from e
        self._seq += 1
        self._size += len(raw)
        return rec

    def close(self) -> None:
        for fd in (self._fd, self._lockfd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._fd = self._lockfd = None


def read_log(path) -> list[dict]:
    return _parse(require_absolute(path).read_bytes())


def explain(path, correlation_id) -> list[dict]:
    """Reconstrói a cadeia de uma decisão lendo só o arquivo."""
    return [r for r in read_log(path) if r["correlation_id"] == correlation_id]
