from dataclasses import asdict, dataclass

from sentinela.trading import store
from sentinela.trading.audit import AuditError
from sentinela.trading.clock import week_key
from sentinela.trading.store import StateCorrupt, StateWriteError, require_absolute

INTEGRITY = "integrity"   # nada passa, nem saída
LIMIT = "limit"           # entrada vetada, saída redutora passa
KILL_SWITCH = "kill_switch"


@dataclass(frozen=True)
class HaltRecord:
    rule: str
    klass: str
    detail: dict
    since: str
    expires: str | None   # "day:AAAA-MM-DD" | "week:AAAA-Wnn" | None (só clear_halt)


class HaltStore:
    """Travas do sistema, em arquivo: gravadas antes de qualquer aprovação, sobrevivem a reinício."""

    def __init__(self, path, clock, audit=None, first_run: bool = False):
        self.path = require_absolute(path)
        self.clock = clock
        self.audit = audit
        self._records: list[HaltRecord] = []
        self._load(first_run)

    def _stamp(self) -> str:
        return self.clock.now().isoformat()

    def _load(self, first_run: bool) -> None:
        if not self.path.exists():
            if first_run:
                self._save()
                return
            self._records = [HaltRecord("estado_ausente", INTEGRITY, {}, self._stamp(), None)]
            self._persist_or_log("estado_ausente")
            return
        try:
            obj = store.read_json(self.path)
            self._records = [HaltRecord(**r) for r in obj["halts"]]
        except (StateCorrupt, KeyError, TypeError):
            # não regrava por cima: o arquivo estragado fica para ser visto
            self._records = [HaltRecord("estado_corrompido", INTEGRITY, {}, self._stamp(), None)]

    def _expiry_key(self, scope: str | None) -> str | None:
        d = self.clock.ny_date()
        if scope == "day":
            return f"day:{d.isoformat()}"
        if scope == "week":
            return f"week:{week_key(d)}"
        return None

    def _alive(self, rec: HaltRecord) -> bool:
        if rec.expires is None:
            return True
        return rec.expires in (f"day:{self.clock.ny_date().isoformat()}",
                               f"week:{week_key(self.clock.ny_date())}")

    def _save(self) -> None:
        store.atomic_write_json(self.path, {"halts": [asdict(r) for r in self._records]})

    def _log(self, type_: str, rule: str, payload: dict) -> None:
        if self.audit is None:
            return
        try:
            self.audit.append(agent="halts", type=type_, correlation_id=f"halt:{rule}",
                              causation_id=None, payload=payload)
        except AuditError:
            pass  # o log quebrado já é um halt por si; não mascarar o halt original

    def _persist_or_log(self, rule: str) -> None:
        """Grava o arquivo; se não conseguir, a trava continua só na memória e isso fica registrado."""
        try:
            self._save()
        except StateWriteError as e:
            self._log("HaltNotPersisted", rule, {"rule": rule, "error": str(e)})

    def active(self) -> list[HaltRecord]:
        return [r for r in self._records if self._alive(r)]

    def integrity(self) -> list[HaltRecord]:
        return [r for r in self.active() if r.klass == INTEGRITY]

    def limit(self) -> list[HaltRecord]:
        return [r for r in self.active() if r.klass == LIMIT]

    def raise_(self, rule: str, klass: str, detail: dict | None = None,
               scope: str | None = None) -> bool:
        """Grava a trava em arquivo antes de voltar. A trava vale na memória mesmo se a gravação falhar."""
        if any(r.rule == rule for r in self.active()):
            return False
        rec = HaltRecord(rule, klass, detail or {}, self._stamp(), self._expiry_key(scope))
        self._records = [r for r in self._records if self._alive(r) and r.rule != rule] + [rec]
        self._log("HaltRaised", rule, {"rule": rule, "klass": klass, "detail": detail or {},
                                       "scope": scope})
        self._save()  # StateWriteError sobe para quem chamou
        return True

    def raise_safe(self, rule: str, klass: str, detail: dict | None = None,
                   scope: str | None = None) -> bool:
        """Como raise_, para quem não pode falhar (bus, Risk): erro de gravação vira registro, não exceção."""
        try:
            return self.raise_(rule, klass, detail, scope)
        except StateWriteError as e:
            self._log("HaltNotPersisted", rule, {"rule": rule, "error": str(e)})
            return True

    def clear(self, who: str, reason: str) -> list[HaltRecord]:
        """Registra, grava o arquivo limpo e só então esvazia a memória: se algo falhar, tudo continua travado."""
        cleared = self.active()
        rules = [r.rule for r in cleared]
        if self.audit is not None:
            self.audit.append(agent="halts", type="HaltCleared", correlation_id="halt:all",
                              causation_id=None, payload={"who": who, "reason": reason, "rules": rules})
        try:
            store.atomic_write_json(self.path, {"halts": []})
        except StateWriteError as e:
            self._log("HaltClearFailed", "all", {"who": who, "rules": rules, "error": str(e)})
            raise
        self._records = []
        return cleared
