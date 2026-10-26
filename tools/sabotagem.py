"""Sabotagem da entrega 1A: quebra o código de propósito, uma mudança por vez; a suíte tem que ficar vermelha.

Cópia do arquivo antes de cada quebra; restaura sempre; resultado gravado linha a linha.
Âncora que não aparece exatamente uma vez = PULADA (conta como problema, não como pega).
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"C:\Geral\projetos\inativos\sentinela-mercado")
PY = ROOT / ".venv" / "Scripts" / "python.exe"
BACKUP = Path(tempfile.mkdtemp(prefix="sabot-bak-"))
OUT = Path(sys.argv[1])
ONLY = set(sys.argv[2:])

T = "sentinela/trading/"
S = [
    # id, critério, arquivo, âncora, troca
    ("S01", "1", T + "bus.py", 'if pub.name not in ALLOWED_PUBLISHERS.get(type_, ()):', 'if False:'),
    ("S02", "1", T + "bus.py", 'if self._keys.get(pub.name) is not pub._key:', 'if False:'),
    ("S03", "1", T + "bus.py", 'elif agent.critical:', 'elif True:'),
    ("S04", "1", T + "bus.py", 'agent.queue.put_nowait(copy.deepcopy(rec))', 'agent.queue.put_nowait(rec)'),
    ("S05", "1", T + "bus.py", 'type="PublishDenied"', 'type="PublishDeniedX"'),
    ("S06", "1", T + "bus.py", 'type="AgentFailed"', 'type="AgentFailedX"'),
    ("S07", "2", T + "bus.py", 'self.halt("fila_cheia", INTEGRITY, {"agent": agent.name})', 'pass'),
    ("S08", "2", T + "bus.py", 'self.halt("handler_lento", INTEGRITY, {"agent": agent.name})', 'pass'),
    ("S09", "2", T + "bus.py", 'self.halt("loop_travado", INTEGRITY, {"limite_s": self.heartbeat_limit})', 'pass'),
    ("S10", "2", T + "bus.py", 'if time.monotonic() - self._last_beat > self.heartbeat_limit:', 'if False:'),
    ("S11", "2", T + "bus.py", 'self.halt(f"agente_{agent.name}_caiu", INTEGRITY, {"error": str(exc)})', 'pass'),
    ("S12", "2", T + "bus.py", 'self.halt("log_falhou", INTEGRITY, {"error": str(e)})\n            raise', 'raise'),
    ('S13', '2', 'sentinela/trading/bus.py', 'self.halt(KILL_SWITCH, LIMIT, {"who": who, "reason": reason})', 'pass'),
    ("S14", "3", T + "risk.py", 'if not (math.isfinite(v) and -1 < v < 0):', 'if False:'),
    ("S15", "3", T + "risk.py", 'if not (math.isfinite(v) and 0 < v <= 1):', 'if False:'),
    ("S16", "3", T + "risk.py", 'return d if d.is_finite() else None', 'return d'),
    ("S17", "3", T + "risk.py", 'if vals["equity"] <= 0:', 'if False:'),
    ("S18", "3", T + "risk.py", 'if vals["peak_equity"] < vals["equity"]:', 'if False:'),
    ("S19", "3", T + "risk.py", 'if qtd is None or qtd <= 0:', 'if qtd is None:'),
    ("S20", "3", T + "risk.py", 'if ticker not in self.cfg.allowed_tickers:', 'if False:'),
    ("S21", "3", T + "risk.py", 'if weight is None or price is None or weight <= 0 or price <= 0:', 'if weight is None or price is None:'),
    ("S22", "3", T + "risk.py", 'self._halt("estado_invalido", INTEGRITY, bad)', 'pass'),
    ('S23', '4', 'sentinela/trading/risk.py', '_SCOPE = {DAILY_LOSS: "day", WEEKLY_LOSS: "week"}', '_SCOPE = {}'),
    ("S24", "4", T + "halts.py", '        if rec.expires is None:\n            return True', '        if True:\n            return True'),
    ("S25", "4", T + "halts.py", '            if first_run:\n                self._save()\n                return', '            if True:\n                self._save()\n                return'),
    ("S26", "4", T + "risk.py", '            if first_run:\n                self._save()\n            else:', '            if True:\n                self._save()\n            else:'),
    ("S27", "4", T + "risk.py", 'self._halt("risk_estado_corrompido", INTEGRITY)', 'pass'),
    ("S28", "4", T + "risk.py", '            self._save()  # em arquivo antes de aprovar', '            pass'),
    ('S29', '4', 'sentinela/trading/risk.py', '            del self.pending[approval_id]\n            if side == "buy":\n                self.trades_today -= 1', '            if side == "buy":\n                self.trades_today -= 1'),
    ("S30", "4", T + "risk.py", 'if self.kill_path.exists():', 'if False:'),
    ('S31', '4', 'sentinela/trading/halts.py', '        self._records = []\n        return cleared', '        return cleared'),
    ("S32", "4", T + "halts.py", '        self._save()  # StateWriteError sobe para quem chamou\n        return True', '        return True'),
    ("S33", "4", T + "risk.py", '        if integ:\n            return veto(integ[0].rule, integ[0].detail)', '        if False:\n            return veto(integ[0].rule, integ[0].detail)'),
    ("S34", "4", T + "risk.py", '        if limit:\n            return veto(limit[0].rule, limit[0].detail)', '        if False:\n            return veto(limit[0].rule, limit[0].detail)'),
    ('S35', '4', 'sentinela/trading/risk.py', 'if halt is not None and halt.rule != TRADES:', 'if False:'),
    ('S36', '4', 'sentinela/trading/risk.py', 'if halt is not None and halt.rule != TRADES:', 'if halt is not None:'),
    ("S37", "4", T + "risk.py", 'self.trade_day, self.trades_today = day, 0', 'self.trade_day = day'),
    ("S38", "4", T + "risk.py", 'self._halt(STATE_WRITE_FAILED, INTEGRITY)\n            return veto(STATE_WRITE_FAILED)', 'return veto(STATE_WRITE_FAILED)'),
    ("S39", "4", T + "halts.py", 'self._records = [HaltRecord("estado_ausente", INTEGRITY, {}, self._stamp(), None)]', 'self._records = []'),
    ("S40", "4", T + "halts.py", 'self._records = [HaltRecord("estado_corrompido", INTEGRITY, {}, self._stamp(), None)]', 'self._records = []'),
    ("S41", "4", T + "risk.py", 'self._halt("risk_estado_ausente", INTEGRITY)', 'pass'),
    ("S42", "5", T + "risk.py", '        if side == "buy":\n            self.trades_today += 1\n        try:', '        if side == "buy":\n            pass\n        try:'),
    ("S43", "5", T + "risk.py", 'if ticker in positions or any(r["ticker"] == ticker and r["side"] == "buy"', 'if ticker in positions or False and any(r["ticker"] == ticker and r["side"] == "buy"'),
    ("S44", "5", T + "risk.py", 'if ticker in positions or any(', 'if False or any('),
    ("S45", "5", T + "risk.py", 'rounding=ROUND_FLOOR', 'rounding="ROUND_HALF_UP"'),
    ("S46", "5", T + "risk.py", 'final = min(requested, D(repr(self.cfg.base.max_position_weight)))', 'final = requested'),
    ("S47", "5", T + "risk.py", 'requested = weight * D(repr(factor))', 'requested = weight'),
    ("S48", "5", T + "risk.py", '        if qty <= 0:', '        if qty < 0:'),
    ("S49", "5", T + "risk.py", 'if total > cap:', 'if total >= cap:'),
    ("S50", "5", T + "risk.py", 'if total > cap:', 'if False:'),
    ("S51", "5", T + "risk.py", 'pending_buys = sum((r["reserve"] for r in self.pending.values() if r["side"] == "buy"), D(0))', 'pending_buys = D(0)'),
    ("S52", "5", T + "risk.py", 'reserve = qty * price', 'reserve = qty'),
    ('S53', '5', 'sentinela/trading/risk.py', '        if filled_qty == 0:', '        if False:'),
    ('S54', '5', 'sentinela/trading/risk.py', '            del self.pending[approval_id]\n            if rec["side"] == "buy" and rec["day"] == self.trade_day:', '            if rec["side"] == "buy" and rec["day"] == self.trade_day:'),
    ('S55', '5', 'sentinela/trading/risk.py', '        if filled_qty == 0:', '        if filled_qty >= 0:'),
    ('S56', '5', 'sentinela/trading/risk.py', 'if halt is not None and halt.rule == TRADES:', 'if False:'),
    ('S57', '5', 'sentinela/trading/risk.py', '        limit = self.halts.limit()\n        if limit:\n            return veto(limit[0].rule, limit[0].detail)\n', '        limit = self.halts.limit()\n        if limit:\n            return veto(limit[0].rule, limit[0].detail)\n        if halt is not None and halt.rule == TRADES:\n            return veto("trades_day", {})\n'),
    ("S58", "6", T + "risk.py", '            return self._sell(sig, qtd, positions, inputs, veto)', '            if self.halts.limit():\n                return veto(self.halts.limit()[0].rule)\n            return self._sell(sig, qtd, positions, inputs, veto)'),
    ("S59", "6", T + "risk.py", 'if qtd > held - open_sells:', 'if qtd > held:'),
    ("S60", "6", T + "risk.py", 'if qtd > held - open_sells:', 'if False:'),
    ("S61", "6", T + "risk.py", 'if pos is None or pos.get("mark") is None:', 'if False:'),
    ("S62", "6", T + "risk.py", 'D(str(pos["mark"])), D(0), inputs', 'D(0), D(0), inputs'),
    ('S63', '6', 'sentinela/trading/risk.py', 'if not isinstance(raw, dict) or side not in ("buy", "sell") or not isinstance(ticker, str):', 'if not isinstance(raw, dict) or not isinstance(ticker, str):'),
    ("S64", "7", T + "portfolio.py", 'avg = (pos["qty"] * pos["avg"] + qty * price) / new_qty', 'avg = price'),
    ("S65", "7", T + "portfolio.py", 'realized = qty * (price - pos["avg"])', 'realized = qty * price'),
    ("S66", "7", T + "portfolio.py", 'if qty > pos["qty"]:', 'if False:'),
    ("S67", "7", T + "portfolio.py", 'self.cycle.pop(ticker) < 0 else 0', 'self.cycle.pop(ticker) <= 0 else 0'),
    ("S68", "7", T + "portfolio.py", 'self.cycle[ticker] = self.cycle.get(ticker, D(0)) + realized', 'self.cycle[ticker] = realized'),
    ("S69", "7", T + "portfolio.py", 'self.day_base = self.last_equity if self.last_equity is not None else eq', 'self.day_base = eq'),
    ("S70", "7", T + "portfolio.py", 'self.week_base = self.last_equity if self.last_equity is not None else eq', 'self.week_base = eq'),
    ("S71", "7", T + "portfolio.py", 'self.peak = eq if self.peak is None else max(self.peak, eq)', 'self.peak = eq'),
    ("S72", "7", T + "portfolio.py", '"marks": {t: {"price": str(m["price"]), "ts": m["ts"]} for t, m in self.marks.items()},', '"marks": {},'),
    ("S73", "7", T + "portfolio.py", 'total += p["qty"] * self.marks[t]["price"]', 'total += p["qty"] * p["avg"]'),
    ("S74", "7", T + "portfolio.py", '            if t not in self.marks:\n                raise MarkMissing(t)', '            if t not in self.marks:\n                pass'),
    ('S75', '7', 'sentinela/trading/portfolio.py', '        if losses:\n            self.losses = 0', '        if False:\n            self.losses = 0'),
    ('S76', '7', 'sentinela/trading/portfolio.py', '        if peak:\n            self.peak = self.equity()', '        if False:\n            self.peak = self.equity()'),
    ("S77", "7", T + "portfolio.py", 'self.bus.halt("fill_negativo", INTEGRITY, {"error": str(e)})', 'pass'),
    ("S78", "7", T + "portfolio.py", 'self.bus.halt("mark_ausente", INTEGRITY, {"ticker": str(e)})', 'pass'),
    ("S79", "7", T + "portfolio.py", 'self.marks.setdefault(ticker, {"price": price, "ts": self.clock.now().isoformat()})', 'pass'),
    ("S80", "8", T + "audit.py", 'f.truncate(cut)', 'pass'),
    ("S81", "8", T + "audit.py", 'if not isinstance(rec, dict) or rec.get("seq") != i:', 'if not isinstance(rec, dict):'),
    ("S82", "8", T + "audit.py", '            _lock(self._lockfd)\n', '            pass\n'),
    ("S83", "8", T + "audit.py", '.astimezone(timezone.utc).isoformat()', '.replace(tzinfo=None).isoformat()'),
    ("S84", "8", T + "audit.py", 'ensure_ascii=False', 'ensure_ascii=True'),
    ("S85", "8", T + "audit.py", '            if trimmed:\n                self.recovered_tail = True', '            if False:\n                self.recovered_tail = True'),
    ("S86", "8", T + "audit.py", 'return obj if obj == obj and obj not in (float("inf"), float("-inf")) else repr(obj)', 'return obj'),
    ("S87", "8", T + "audit.py", '        self._seq += 1\n        self._size += len(raw)', '        self._size += len(raw)'),
    ("S88", "8", T + "audit.py", '        raw = _canonical(rec) + b"\\n"\n        try:\n            self._write(raw)', '        self._seq += 1\n        raw = _canonical(rec) + b"\\n"\n        try:\n            self._write(raw)'),
    ("S89", "9", T + "audit.py", 'return [r for r in read_log(path) if r["correlation_id"] == correlation_id]', 'return [r for r in read_log(path)]'),
    ("S90", "9", T + "risk.py", '        self.view = json.loads(json.dumps(view))  # cópia: ninguém mexe no que o Risk viu', '        self.view = view'),
    ('S91', '9', 'sentinela/trading/risk.py', '        self.pub.publish(RISK_RESET_REQUESTED, {\n            "peak": DRAWDOWN in rules, "losses": CONSECUTIVE_LOSSES in rules,\n            "who": who, "reason": reason, "reset_id": reset_id})', '        pass'),
    ('S92', '9', 'sentinela/trading/risk.py', 'ev["correlation_id"], ev["seq"])\n            except Exception:', 'ev["correlation_id"], None)\n            except Exception:'),
    ("S98", "1", T + "events.py", 'SIGNAL_PROPOSED = "SignalProposed"', 'SIGNAL_PROPOSED = "SignalProposedX"'),
    ("S99", "1", T + "bus.py", '    ORDER_APPROVED: {"risk"},', '    ORDER_APPROVED: {"strategy"},'),
    ("S100", "2", T + "bus.py", 'BEATS_PER_LIMIT = 4 ', 'BEATS_PER_LIMIT = 0.5 '),
    ('S93', '10', 'sentinela/strategy/risk_controller.py', 'DAILY_LOSS: "perda diária no limite ({:.2%})"', 'DAILY_LOSS: "perda diaria no limite ({:.2%})"'),
    ("S94", "10", "sentinela/strategy/risk_controller.py", 'return max(0.0, 1.0 - abs(drawdown) / abs(max_drawdown))', 'return 1.0 - abs(drawdown) / abs(max_drawdown)'),
    ("S95", "10", "sentinela/strategy/risk_controller.py", 'if state.daily_pnl <= cfg.max_daily_loss:', 'if state.daily_pnl < cfg.max_daily_loss:'),
    ('S96', '10', 'sentinela/strategy/risk_controller.py', 'return Halt(CONSECUTIVE_LOSSES, state.consecutive_losses)', 'return Halt(TRADES, state.consecutive_losses)'),
    ("S97", "10", "sentinela/strategy/risk_controller.py", '        factor = drawdown_factor(state.equity, state.peak_equity, cfg.max_drawdown)', '        factor = 1.0'),
    ('S101', 'C1', 'sentinela/trading/risk.py', 'if self.view.get("session_date") != today:', 'if False:'),
    ('S102', 'C2a', 'sentinela/trading/risk.py', '"peak": DRAWDOWN in rules, "losses": CONSECUTIVE_LOSSES in rules,', '"peak": True, "losses": True,'),
    ('S103', 'C2a', 'sentinela/trading/risk.py', '"peak": DRAWDOWN in rules, "losses": CONSECUTIVE_LOSSES in rules,', '"peak": False, "losses": False,'),
    ('S104', 'C2b', 'sentinela/trading/risk.py', '        self._awaiting_reset = uuid.uuid4().hex', '        self._awaiting_reset = None'),
    ('S105', 'C2b', 'sentinela/trading/risk.py', '        self.view = None\n        self._awaiting_reset = uuid.uuid4().hex', '        self._awaiting_reset = uuid.uuid4().hex'),
    ('S106', 'C2b', 'sentinela/trading/risk.py', 'if self._awaiting_reset is not None and view.get("reset_id") != self._awaiting_reset:', 'if False:'),
    ('S107', 'C2c', 'sentinela/trading/halts.py', '        cleared = self.active()\n        rules = [r.rule for r in cleared]', '        cleared = self.active()\n        self._records = []\n        rules = [r.rule for r in cleared]'),
    ('S108', 'C2c', 'sentinela/trading/halts.py', '            self.audit.append(agent="halts", type="HaltCleared", correlation_id="halt:all",\n                              causation_id=None, payload={"who": who, "reason": reason, "rules": rules})', '            self._log("HaltCleared", "all", {"who": who, "reason": reason, "rules": rules})'),
    ('S109', 'C2c', 'sentinela/trading/halts.py', '            self._log("HaltClearFailed", "all", {"who": who, "rules": rules, "error": str(e)})\n            raise', '            raise'),
    ('S110', 'C3', 'sentinela/trading/risk.py', '            rec["closed_seq"] = seq if seq is not None else 0', '            del self.pending[approval_id]'),
    ('S111', 'C3', 'sentinela/trading/risk.py', 'if r["closed_seq"] is not None and applied >= r["closed_seq"]]', 'if r["closed_seq"] is not None]'),
    ('S112', 'C3', 'sentinela/trading/portfolio.py', 'subscribes=[FILL, MARK_PRICE, ORDER_CLOSED, RISK_RESET_REQUESTED]', 'subscribes=[FILL, MARK_PRICE, RISK_RESET_REQUESTED]'),
    ('S113', 'C3', 'sentinela/trading/portfolio.py', 'view["applied_seq"] = seq', 'view["applied_seq"] = 0'),
    ('S114', 'C4', 'sentinela/trading/portfolio.py', 'if side not in ("buy", "sell") or not _positive(qty) or not _positive(price):', 'if False:'),
    ('S115', 'C4', 'sentinela/trading/portfolio.py', 'return isinstance(x, D) and x.is_finite() and x > 0', 'return isinstance(x, D) and x != 0'),
    ('S116', 'C4', 'sentinela/trading/portfolio.py', '        if not _positive(price):\n            raise InvalidFill(f"marca inválida', '        if False:\n            raise InvalidFill(f"marca inválida'),
    ('S117', 'C4', 'sentinela/trading/portfolio.py', 'self.bus.halt(invalid_rule, INTEGRITY, {"error": repr(e)})', 'pass'),
    ('S118', 'C5', 'sentinela/trading/risk.py', 'self.risk.on_order_closed(payload["approval_id"], D(0))', 'pass'),
    ('S119', 'C6', 'sentinela/trading/bus.py', '        if name in self._agents:', '        if False:'),
    ('S120', 'C7a', 'sentinela/trading/store.py', '    if not p.is_absolute():', '    if False:'),
    ('S121', 'C7b', 'sentinela/trading/risk.py', '            self.trade_day, self.trades_today = o["trade_day"], int(o["trades_today"])', '            pass'),
    ('S122', 'C7b', 'sentinela/trading/risk.py', '                            for k, r in o["pending"].items()}', '                            for k, r in {}.items()}'),
    ('S123', 'C7c', 'sentinela/trading/bus.py', 'QUEUE_MAX = 100', 'QUEUE_MAX = 1000000'),
    ('S124', 'C7c', 'sentinela/trading/bus.py', 'HANDLER_TIMEOUT_S = 5.0', 'HANDLER_TIMEOUT_S = 500.0'),
    ('S125', 'C7c', 'sentinela/trading/bus.py', 'HEARTBEAT_LIMIT_S = 5.0', 'HEARTBEAT_LIMIT_S = 500.0'),
    ('S126', 'C8', 'sentinela/trading/bus.py', 'self.halt("vigia_falhou", INTEGRITY, {"error": self._watch_error})', 'pass'),
    ('S127', 'C8', 'sentinela/trading/bus.py', 'self._watch_error = str(e)  # quem registra é o loop, quando destravar', 'pass'),
    ('S128', 'C8', 'sentinela/trading/halts.py', '            self._log("HaltNotPersisted", rule, {"rule": rule, "error": str(e)})\n            return True', '            return True'),
    ('S129', 'C8', 'sentinela/trading/halts.py', '            self._log("HaltNotPersisted", rule, {"rule": rule, "error": str(e)})\n\n', '            pass\n\n'),
    ('S130', 'C9', 'sentinela/trading/portfolio.py', 'self._apply(None, None, 0, lambda: None, "portfolio_invalido")', 'self.pub.publish(PORTFOLIO_UPDATED, self.portfolio.view(), None, None)'),
    ('S131', 'R', 'sentinela/trading/risk.py', '        else:  # sair tem de ser possível mesmo se o ticker saiu da lista: vale a posição\n            qtd = _dec(p.get("qtd"))', '        else:\n            if ticker not in self.cfg.allowed_tickers:\n                return veto("ticker_fora_da_lista")\n            qtd = _dec(p.get("qtd"))'),
    ('S132', 'R', 'sentinela/trading/halts.py', 'KILL_SWITCH = "kill_switch"', 'KILL_SWITCH = "kill_switch_x"'),
    ('S133', 'C2', 'sentinela/trading/portfolio.py', '        if reset_id is not None:\n            self.reset_id = reset_id', '        if False:\n            self.reset_id = reset_id'),
    ('S134', 'C3', 'sentinela/trading/risk.py', '        self._save_or_halt()\n        self._release_applied()\n\n    def clear_halt', '        self._save_or_halt()\n\n    def clear_halt'),
    ('S135', 'R', 'sentinela/trading/risk.py', ' or not isinstance(ticker, str):', ':'),
]


def run_pytest():
    env = dict(os.environ)
    env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="sabot-pyc-")
    try:
        p = subprocess.run([str(PY), "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "PEGA(travou)", "timeout"
    finally:
        shutil.rmtree(env["PYTHONPYCACHEPREFIX"], ignore_errors=True)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith(("FAILED", "ERROR"))]
    first = lines[0][:150] if lines else p.stdout.strip().splitlines()[-1][:150]
    return ("VERDE" if p.returncode == 0 else "PEGA"), first


def main():
    OUT.write_text("", encoding="utf-8")
    out = open(OUT, "a", encoding="utf-8", buffering=1)  # linha a linha
    totals = {"PEGA": 0, "VERDE": 0, "PULADA": 0}
    for sid, crit, rel, old, new in S:
        if ONLY and sid not in ONLY:
            continue
        path = ROOT / rel
        raw = path.read_bytes()
        crlf = b"\r\n" in raw
        o = old.replace("\n", "\r\n") if crlf else old
        n = new.replace("\n", "\r\n") if crlf else new
        text = raw.decode("utf-8")
        if text.count(o) != 1:
            status, info = "PULADA", f"âncora aparece {text.count(o)}x"
        else:
            shutil.copy2(path, BACKUP / (sid + "_" + path.name))
            try:
                path.write_bytes(text.replace(o, n).encode("utf-8"))
                status, info = run_pytest()
            finally:
                path.write_bytes(raw)  # restaura sempre
        key = "PEGA" if status.startswith("PEGA") else status
        totals[key] += 1
        out.write(f"{sid} crit{crit} {status} | {rel.split('/')[-1]} | {info}\n")
    out.write(f"TOTAL pegas={totals['PEGA']} verdes={totals['VERDE']} puladas={totals['PULADA']}\n")
    out.close()


main()
