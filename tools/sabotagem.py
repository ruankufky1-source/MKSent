"""Sabotagem das entregas 1A e 1B: quebra o código de propósito, uma mudança por vez; a suíte tem que ficar vermelha.

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
    ('S54', '5', 'sentinela/trading/risk.py', '        if filled_qty == 0:  # nada mudou na carteira\n            self._drop(approval_id, rec)', '        if filled_qty == 0:  # nada mudou na carteira\n            pass'),
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
    ('S134', 'C3', 'sentinela/trading/risk.py', '        self._save_or_halt()\n        self._release_applied()\n        return True', '        self._save_or_halt()\n        return True'),
    ('S135', 'R', 'sentinela/trading/risk.py', ' or not isinstance(ticker, str):', ':'),
    # --- 1B -------------------------------------------------------------------------------------
    ('S136', 'B1', T + 'broker.py', 'if req.qty > self.cfg.max_qty:', 'if False:'),
    ('S137', 'B1', T + 'broker.py', 'if value > self.cfg.max_order_value:', 'if value >= self.cfg.max_order_value:'),
    ('S138', 'B1', T + 'broker.py', 'if value > self.cfg.max_order_value:', 'if False:'),
    ('S139', 'B1', T + 'broker.py', 'if req.ticker not in self.cfg.allowed_tickers:', 'if False:'),
    ('S140', 'B1', T + 'broker.py', '        if req.side == "sell":\n            return', '        if False:\n            return'),
    ('S141', 'B1', T + 'broker.py', 'if not _finite_positive(req.qty):', 'if req.qty is None:'),
    ('S142', 'B1', T + 'broker.py', 'if not _finite_positive(req.ref_price):', 'if req.ref_price is None:'),
    ('S143', 'B1', T + 'broker.py', 'if req.side not in ("buy", "sell"):', 'if False:'),
    ('S144', 'B1', T + 'broker.py', 'return isinstance(x, D) and x.is_finite() and x > 0', 'return isinstance(x, D) and x.is_finite() and x >= 0'),
    ('S145', 'B1', T + 'broker.py', '        if not self.allowed_tickers:', '        if False:'),
    ('S146', 'B1', T + 'broker.py', '        self._check(req)\n        return await', '        return await'),
    ('S147', 'B1', T + 'broker.py', 'if not _finite_positive(self.max_order_value) or not _finite_positive(self.max_qty):', 'if not _finite_positive(self.max_order_value):'),
    ('S148', 'B6', T + 'simbroker.py', 'partial = self._rng.random() < self.partial_prob', 'partial = self._rng.random() < self.partial_prob * 2'),
    ('S149', 'B4', T + 'simbroker.py', '        if req.client_order_id in self.orders:\n            raise DuplicateOrderId', '        if False:\n            raise DuplicateOrderId'),
    ('S150', 'B6', T + 'simbroker.py', 'if partial and req.qty >= 2:', 'if partial:'),
    ('S151', 'B6', T + 'simbroker.py', 'first = req.qty // 2', 'first = req.qty // 3'),
    ('S152', 'B6', T + 'simbroker.py', '        if self._pos[t] == 0:\n            del self._pos[t]', '        if False:\n            del self._pos[t]'),
    ('S153', 'E1', T + 'execution.py', 'if not isinstance(guard, GuardedBroker):', 'if False:'),
    ('S154', 'E2', T + 'execution.py', 'return f"{ID_PREFIX}-{approved_seq}-{ticker}-{side}"', 'return f"{ID_PREFIX}-{ticker}-{side}"'),
    ('S155', 'E2', T + 'execution.py', '            if cid in self.orders:\n                self.pub.publish(ORDER_EVENT_IGNORED', '            if False:\n                self.pub.publish(ORDER_EVENT_IGNORED'),
    ('S156', 'E5', T + 'execution.py', 'if not self.reconciled:\n                self._not_sent(rec, "sem_reconciliacao")', 'if False:\n                self._not_sent(rec, "sem_reconciliacao")'),
    ('S157', 'E5', T + 'execution.py', 'if not 0 <= age <= self.approval_ttl:', 'if not 0 <= age < self.approval_ttl:'),
    ('S158', 'E5', T + 'execution.py', 'if not 0 <= age <= self.approval_ttl:', 'if not age <= self.approval_ttl:'),
    ('S159', 'E5', T + 'execution.py', 'if not 0 <= age <= self.approval_ttl:', 'if not 0 <= age:'),
    ('S161', 'E3', T + 'execution.py', '            self.pub.publish(ORDER_INTENT, {\n                "client_order_id": cid, "approval_id": rec["approval_id"], "ticker": ticker,\n                "side": side, "qtd": str(qty), "ref_price": str(price)}, ev["correlation_id"], ev["seq"])\n', '            pass\n'),
    ('S162', 'E5', T + 'execution.py', '            if self.kill_path.exists():', '            if False:'),
    ('S163', 'E5', T + 'execution.py', '            if self.halts.integrity():\n                blocked = "halt_integridade"', '            if False:\n                blocked = "halt_integridade"'),
    ('S164', 'E5', T + 'execution.py', 'elif side == "buy" and self.halts.limit():', 'elif self.halts.limit():'),
    ('S165', 'E5', T + 'execution.py', 'elif side == "buy" and self.halts.limit():', 'elif False:'),
    ('S166', 'E5', T + 'execution.py', '            if blocked:\n                self._not_sent(rec, blocked)\n                return', '            if blocked:\n                return'),
    ('S168', 'E4', T + 'execution.py', '            async with asyncio.timeout(self.broker_timeout):\n                order = await self.guard.submit(req)', '            if True:\n                order = await self.guard.submit(req)'),
    ('S169', 'E1', T + 'execution.py', 'self.bus.halt("guard_recusou", INTEGRITY, {"rule": e.rule, **e.detail})', 'pass'),
    ('S170', 'E1', T + 'execution.py', 'self._not_sent(rec, f"guard:{e.rule}")', 'pass'),
    ('S171', 'E4', T + 'execution.py', 'outcome, order = "duplicate_adopted", await self._resolve(cid, rec)', 'outcome, order = "duplicate_adopted", None'),
    ('S172', 'E4', T + 'execution.py', 'outcome, order = "found_after_timeout", await self._resolve(cid, rec)', 'outcome, order = "found_after_timeout", None'),
    ('S173', 'E4', T + 'execution.py', '_BROKER_ERRORS = (TimeoutError, BrokerError, OSError)', '_BROKER_ERRORS = (TimeoutError, BrokerError)'),
    ('S174', 'E4', T + 'execution.py', '        rec["state"] = AMBIGUOUS\n        self._persist()', '        self._persist()'),
    ('S175', 'E4', T + 'execution.py', '            self.bus.halt("envio_ambiguo", INTEGRITY, {"client_order_id": cid})', '            pass'),
    ('S176', 'E4', T + 'execution.py', '        if order is not None:\n            self._adopt(cid, rec, order, outcome)', '        if order is not None and False:\n            self._adopt(cid, rec, order, outcome)'),
    ('S177', 'E4', T + 'execution.py', 'if (order.ticker, order.side, order.qty) != (rec["ticker"], rec["side"], qty):', 'if False:'),
    ('S178', 'E6', T + 'execution.py', 'if order.status not in OPEN_STATUSES | TERMINAL:', 'if False:'),
    ('S179', 'E6', T + 'execution.py', 'or f > qty:', ':'),
    ('S180', 'E6', T + 'execution.py', '        if f < published:', '        if False:'),
    ('S181', 'E6', T + 'execution.py', '        if f > 0 and not (isinstance(order.filled_avg_price, D)', '        if False and not (isinstance(order.filled_avg_price, D)'),
    ('S182', 'E6', T + 'execution.py', 'if order.status == "filled" and f != qty:', 'if False:'),
    ('S183', 'E6', T + 'execution.py', 'if not price.is_finite() or price <= 0:', 'if not price.is_finite():'),
    ('S184', 'E6', T + 'execution.py', 'price = (order.filled_qty * order.filled_avg_price - pub_v) / delta', 'price = order.filled_avg_price'),
    ('S185', 'E6', T + 'execution.py', 'self._published[aid] = (order.filled_qty, pub_v + delta * price)', 'self._published[aid] = (order.filled_qty, pub_v)'),
    ('S186', 'E6', T + 'execution.py', '            if aid not in self._closed_in_log:\n                self._close(rec, order.status, order.filled_qty)', '            self._close(rec, order.status, order.filled_qty)'),
    ('S187', 'E6', T + 'execution.py', '            rec["state"] = CLOSED\n            self._persist()', '            self._persist()'),
    ('S188', 'E6', T + 'execution.py',
     '        if delta > 0:\n            self.pub.publish(FILL, {"approval_id": aid, "ticker": rec["ticker"], "side": rec["side"],\n                                    "qty": str(delta), "price": str(price)},\n                             rec["correlation_id"], rec["approved_seq"])\n            self._published[aid] = (order.filled_qty, pub_v + delta * price)\n        if order.status in TERMINAL:\n            if aid not in self._closed_in_log:\n                self._close(rec, order.status, order.filled_qty)\n',
     '        if order.status in TERMINAL:\n            if aid not in self._closed_in_log:\n                self._close(rec, order.status, order.filled_qty)\n        if delta > 0:\n            self.pub.publish(FILL, {"approval_id": aid, "ticker": rec["ticker"], "side": rec["side"],\n                                    "qty": str(delta), "price": str(price)},\n                             rec["correlation_id"], rec["approved_seq"])\n            self._published[aid] = (order.filled_qty, pub_v + delta * price)\n        if order.status in TERMINAL:\n'),
    ('S189', 'E6', T + 'execution.py', '"qty": str(delta), "price": str(price)},\n                             rec["correlation_id"], rec["approved_seq"])', '"qty": str(delta), "price": str(price)},\n                             None, None)'),
    ('S190', 'E5', T + 'execution.py', '        if rec["approval_id"] not in self._closed_in_log:\n            self._close(rec, status, D(0), reason)', '        pass'),
    ('S191', 'E6', T + 'execution.py', 'for cid in [c for c, r in self.orders.items() if r["state"] == OPEN]:', 'for cid in [c for c, r in self.orders.items() if r["state"] != CLOSED]:'),
    ('S192', 'E6', T + 'execution.py', '                    self.pub.publish(POLL_FAILED, {"client_order_id": cid, "error": why},\n                                     rec["correlation_id"], rec["approved_seq"])', '                    pass'),
    ('S193', 'E6', T + 'execution.py', '                self.bus.halt("agente_execution_caiu", INTEGRITY, {"error": _describe(e)})\n                return', '                return'),
    ('S194', 'E7', T + 'execution.py', '            async with self._lock(cid):\n                rec = self.orders[cid]\n                try:\n                    order = await self._lookup(cid)\n                except _BROKER_ERRORS as e:\n                    order, why', '            async with asyncio.Lock():\n                rec = self.orders[cid]\n                try:\n                    order = await self._lookup(cid)\n                except _BROKER_ERRORS as e:\n                    order, why'),
    ('S195', 'E7', T + 'execution.py', '            async with self._lock(cid):\n                rec = self.orders[cid]\n                if rec["state"] not in _LIVE:', '            async with asyncio.Lock():\n                rec = self.orders[cid]\n                if rec["state"] not in _LIVE:'),
    ('S196', 'R8', T + 'execution.py', '        self._close_approvals_without_state()\n', '        pass\n'),
    ('S197', 'R8', T + 'execution.py', '        await self.bus.drain()\n        divs += await self._compare()', '        divs += await self._compare()'),
    ('S198', 'R8', T + 'execution.py', '                        self._not_sent(rec, "nao_encontrada_na_reconciliacao")', '                        pass'),
    ('S199', 'R8', T + 'execution.py', '                        divs.append({"kind": "ordem_sumiu", "client_order_id": cid})', '                        pass'),
    ('S200', 'R8', T + 'execution.py', '                if rec["state"] == INVALID:', '                if False:'),
    ('S201', 'R8', T + 'execution.py', '        if divs and halt:', '        if False:'),
    ('S202', 'R8', T + 'execution.py', '        if not divs:\n            self.reconciled = True\n', '        if True:\n            self.reconciled = True\n'),
    ('S203', 'R8', T + 'execution.py', '            if b != m:', '            if False:'),
    ('S204', 'R8', T + 'execution.py', '            if rec is None:\n                divs.append({"kind": "ordem_desconhecida"', '            if False:\n                divs.append({"kind": "ordem_desconhecida"'),
    ('S205', 'R8', T + 'execution.py', '            elif rec["state"] in (CLOSED, NOT_SENT):', '            elif False:'),
    ('S206', 'R8', T + 'execution.py', '                    divs.append({"kind": "corretora_indisponivel", "error": str(e)})\n                    break', '                    break'),
    ('S207', 'R8', T + 'execution.py', '            return [{"kind": "corretora_indisponivel", "error": str(e)}]', '            return []'),
    ('S208', 'R8', T + 'execution.py', '                if rec["state"] in (INTENT, AMBIGUOUS):\n                    self._adopt(cid, rec, order, "adopted_on_reconcile")', '                if False:\n                    self._adopt(cid, rec, order, "adopted_on_reconcile")'),
    ('S209', 'R8', T + 'execution.py', '            if r["type"] != ORDER_APPROVED or p.get("approval_id") in self._closed_in_log:', '            if r["type"] != ORDER_APPROVED:'),
    ('S210', 'R9', T + 'execution.py', '        if not self.halts.integrity():\n            return risk_agent.clear_halt(who, reason)', '        if True:\n            return risk_agent.clear_halt(who, reason, reconciled=True)'),
    ('S211', 'R9', T + 'execution.py', '        if divs:\n            self.pub.publish(HALT_CLEAR_REFUSED', '        if False:\n            self.pub.publish(HALT_CLEAR_REFUSED'),
    ('S212', 'R9', T + 'execution.py', '            self.pub.publish(HALT_CLEAR_REFUSED, {"who": who, "reason": reason, "divergences": divs},\n                             correlation_id="halt:all")', '            pass'),
    ('S213', 'R9', T + 'execution.py', '            raise ReconciliationFailed(divs)', '            pass'),
    ('S214', 'E6', T + 'execution.py', '                self._closed_in_log.add(p["approval_id"])', '                pass'),
    ('S215', 'E6', T + 'execution.py', 'self._published[p["approval_id"]] = (q + D(p["qty"]), v + D(p["qty"]) * D(p["price"]))', 'self._published[p["approval_id"]] = (D(p["qty"]), v + D(p["qty"]) * D(p["price"]))'),
    ('S216', 'R8', T + 'execution.py', 'self.halts.raise_safe("execution_estado_ausente", INTEGRITY)', 'pass'),
    ('S217', 'R8', T + 'execution.py', 'self.halts.raise_safe("execution_estado_corrompido", INTEGRITY)', 'pass'),
    ('S218', 'E1', T + 'execution.py', '            self.bus.halt("aprovacao_invalida", INTEGRITY, {"payload": str(p)[:LOG_PAYLOAD_CHARS]})', '            pass'),
    ('S219', 'E5', 'sentinela/trading/clock.py', 'self._offset_s += seconds', 'self._offset_s -= seconds'),
    ('S220', 'E3', 'sentinela/trading/bus.py', '    ORDER_INTENT: {"execution"},', '    ORDER_INTENT: {"strategy"},'),
    ('S221', 'R10', T + 'risk.py', '        if rec is None or rec["closed_seq"] is not None:\n            return False', '        if rec is None:\n            return False'),
    ('S222', 'R7', T + 'risk.py', '        rec["fills"].append((seq, qty))', '        pass'),
    ('S223', 'R7', T + 'risk.py', 'done = sum((q for s, q in rec["fills"] if s <= applied), D(0))', 'done = sum((q for s, q in rec["fills"]), D(0))'),
    ('S224', 'R7', T + 'risk.py', 'done = sum((q for s, q in rec["fills"] if s <= applied), D(0))', 'done = D(0)'),
    ('S225', 'R7', T + 'risk.py', 'return max(D(0), rec["qty"] - done)', 'return rec["qty"] - done'),
    ('S226', 'R10', T + 'risk.py', '        if rec["side"] == "buy" and rec["day"] == self.trade_day:\n            self.trades_today = max(0, self.trades_today - 1)', '        if rec["side"] == "buy":\n            self.trades_today = max(0, self.trades_today - 1)'),
    ('S227', 'R10', T + 'risk.py', '        if rec["side"] == "buy" and rec["day"] == self.trade_day:\n            self.trades_today = max(0, self.trades_today - 1)', '        if rec["day"] == self.trade_day:\n            self.trades_today = max(0, self.trades_today - 1)'),
    ('S228', 'R10', T + 'risk.py', 'orphans = [k for k in self.pending if k not in logged_approvals]', 'orphans = [k for k in self.pending if k in logged_approvals]'),
    ('S229', 'R9', T + 'risk.py', '        if self.halts.integrity() and not reconciled:', '        if False:'),
    ('S230', 'R9', T + 'risk.py', '        if self.halts.integrity() and not reconciled:', '        if self.halts.integrity():'),
    ('S231', 'R7', T + 'risk.py', 'subscribes=[SIGNAL_PROPOSED, PORTFOLIO_UPDATED, ORDER_CLOSED, FILL])', 'subscribes=[SIGNAL_PROPOSED, PORTFOLIO_UPDATED, ORDER_CLOSED])'),
    ('S232', 'R10', T + 'risk.py', '            if not self.risk.on_order_closed(p["approval_id"], qty, ev["seq"]):', '            if False:'),
    ('S233', 'R10', T + 'risk.py', '            if not self.risk.on_fill(p["approval_id"], qty, ev["seq"]):', '            if False:'),
    ('S234', 'R10', T + 'risk.py', 'if r["type"] == ORDER_APPROVED}', 'if r["type"] == ORDER_VETOED}'),
    ('S235', 'R10', T + 'risk.py', '            self.pub.publish(PENDING_ORPHANED, {"approval_id": approval_id},\n                             correlation_id=f"orphan:{approval_id}")', '            pass'),
    ('S236', 'R7', T + 'risk.py', '"fills": [(int(s), D(q)) for s, q in r.get("fills", [])]}', '"fills": []}'),
    ('S237', 'R7', T + 'risk.py', '"fills": [[s, str(q)] for s, q in r["fills"]]}', '"fills": []}'),
    ('S238', 'R11', T + 'execution.py', '            self._frozen = True', '            pass'),
    ('S239', 'R11', T + 'execution.py', '        if self._frozen:', '        if False:'),
    ('S240', 'R11', T + 'execution.py', '            if not self.path.exists():   # estado ausente', '            if False:   # estado ausente'),
]


def run_pytest():
    env = dict(os.environ)
    env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="sabot-pyc-")
    p = None
    try:
        for _ in range(2):  # o Windows às vezes trava ao criar o laço de eventos: só vale travar duas vezes
            try:
                p = subprocess.run([str(PY), "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"],
                                   cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
                break
            except subprocess.TimeoutExpired:
                continue
        else:
            return "PEGA(travou)", "timeout duas vezes"
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
