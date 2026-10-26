import json
import os
from pathlib import Path


class StateWriteError(Exception):
    pass


class StateCorrupt(Exception):
    pass


def require_absolute(path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        raise ValueError(f"caminho de estado precisa ser absoluto: {path}")
    return p


def atomic_write_json(path, obj) -> None:
    """Grava em arquivo temporário, fsync, e troca de nome: nunca deixa meio arquivo."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    try:
        data = json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1).encode("utf-8")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except OSError as e:
        raise StateWriteError(str(e)) from e


def read_json(path):
    try:
        return json.loads(Path(path).read_bytes().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise StateCorrupt(str(e)) from e
