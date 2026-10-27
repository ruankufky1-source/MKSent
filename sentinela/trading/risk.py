import json
import math
import uuid
from dataclasses import dataclass
from decimal import Decimal as D, InvalidOperation, ROUND_FLOOR
from pathlib import Path

from sentinela.schemas import RiskState
from sentinela.strategy.risk_controller import (
    CONSECUTIVE_LOSSES, DAILY_LOSS, DRAWDOWN, TRADES, WEEKLY_LOSS, RiskConfig, drawdown_factor,
    global_halt,
)
from sentinela.trading import store
from sentinela.trading.audit import read_log
from sentinela.trading.events import (
    FILL, ORDER_APPROVED, ORDER_CLOSED, ORDER_EVENT_IGNORED, ORDER_VETOED, PENDING_ORPHANED,
    PORTFOLIO_UPDATED, RISK_RESET_REQUESTED, SIGNAL_PROPOSED,
)
from sentinela.trading.halts import INTEGRITY, KILL_SWITCH, LIMIT
from sentinela.trading.store import StateCorrupt, StateWriteError, require_absolute

# Filtros do apply_risk que o agente ainda não tem fonte de dados para aplicar; vão em todo registro.
FILTROS_INATIVOS = ["earnings_blackout", "min_interval"]
STATE_WRITE_FAILED = "state_write_failed"

_SCOPE = {DAILY_LOSS: "day", WEEKLY_LOSS: "week"}


class ReconciliationRequired(Exception):
    """clear_halt de uma trava de integridade sem a corretora ter sido conferida antes."""


@dataclass(frozen=True)
class AgentRiskConfig:
    base: RiskConfig
    max_total_exposure: float      # fração do equity, ex.: 0.50
    allowed_tickers: frozenset

    def __post_init__(self):
        b = self.base
        for name in ("max_daily_loss", "max_weekly_loss", "max_drawdown"):
            v = getattr(b, name)
            if not (math.isfinite(v) and -1 < v < 0):
                raise ValueError(f"{name} precisa ser fração negativa em (-1, 0): {v}")
        for name, v in (("max_position_weight", b.max_position_weight),
                        ("max_total_exposure", self.max_total_exposure)):
            if not (math.isfinite(v) and 0 < v <= 1):
                raise ValueError(f"{name} precisa estar em (0, 1]: {v}")
        if b.max_consecutive_losses < 1 or b.max_trades_per_day < 1:
            raise ValueError("limites de contagem precisam ser >= 1")
        if not self.allowed_tickers:
            raise ValueError("lista de tickers vazia")


def _dec(x) -> D | None:
    try:
        d = D(str(x))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


class Risk:
    """Único que aprova e dimensiona. Estado em arquivo, gravado antes de qualquer aprovação."""

    def __init__(self, cfg: AgentRiskConfig, halts, state_path, clock, kill_path,
                 first_run: bool = False):
        self.cfg, self.halts, self.clock = cfg, halts, clock
        self.path = require_absolute(state_path)
        self.kill_path = Path(kill_path)
        self.view: dict | None = None
        self._awaiting_reset: str | None = None   # depois de clear_halt: só vale a foto com este reset_id
        self.trade_day = clock.ny_date().isoformat()
        self.trades_today = 0
        self.pending: dict[str, dict] = {}
        self._load(first_run)

    # --- estado ---------------------------------------------------------------------------

    def _halt(self, rule, klass, detail=None, scope=None) -> None:
        self.halts.raise_safe(rule, klass, detail, scope)

    def _load(self, first_run: bool) -> None:
        if not self.path.exists():
            if first_run:
                self._save()
            else:
                self._halt("risk_estado_ausente", INTEGRITY)
            return
        try:
            o = store.read_json(self.path)
            self.trade_day, self.trades_today = o["trade_day"], int(o["trades_today"])
            self.pending = {k: {"side": r["side"], "ticker": r["ticker"], "qty": D(r["qty"]),
                                "reserve": D(r["reserve"]), "day": r["day"],
                                "closed_seq": r["closed_seq"],
                                "fills": [(int(s), D(q)) for s, q in r.get("fills", [])]}
                            for k, r in o["pending"].items()}
        except (StateCorrupt, KeyError, TypeError, ValueError, InvalidOperation):
            self._halt("risk_estado_corrompido", INTEGRITY)

    def _save(self) -> None:
        store.atomic_write_json(self.path, {
            "trade_day": self.trade_day, "trades_today": self.trades_today,
            "pending": {k: {"side": r["side"], "ticker": r["ticker"], "qty": str(r["qty"]),
                            "reserve": str(r["reserve"]), "day": r["day"],
                            "closed_seq": r["closed_seq"],
                            "fills": [[s, str(q)] for s, q in r["fills"]]}
                        for k, r in self.pending.items()},
        })

    def _save_or_halt(self) -> None:
        try:
            self._save()
        except StateWriteError:
            self._halt(STATE_WRITE_FAILED, INTEGRITY)

    def _roll_day(self) -> None:
        day = self.clock.ny_date().isoformat()
        if day != self.trade_day:
            self.trade_day, self.trades_today = day, 0

    # --- eventos que chegam ---------------------------------------------------------------

    def on_view(self, view: dict) -> None:
        if self._awaiting_reset is not None and view.get("reset_id") != self._awaiting_reset:
            return  # foto anterior ao clear_halt: reviveria a trava que acabou de ser limpa
        self._awaiting_reset = None
        self.view = json.loads(json.dumps(view))  # cópia: ninguém mexe no que o Risk viu
        self._release_applied()

    def _release_applied(self) -> None:
        """Solta a ordem fechada quando a foto da carteira já inclui os fills dela."""
        if self.view is None:
            return
        applied = self.view.get("applied_seq", 0)
        done = [k for k, r in self.pending.items()
                if r["closed_seq"] is not None and applied >= r["closed_seq"]]
        for k in done:
            del self.pending[k]
        if done:
            self._save_or_halt()

    def _drop(self, approval_id: str, rec: dict) -> None:
        """Solta a reserva e, se era compra do dia, o trade."""
        del self.pending[approval_id]
        if rec["side"] == "buy" and rec["day"] == self.trade_day:
            self.trades_today = max(0, self.trades_today - 1)

    def on_order_closed(self, approval_id: str, filled_qty: D, seq: int | None = None) -> bool:
        """False se o id é desconhecido ou o fechamento já foi recebido (o agente registra no log)."""
        rec = self.pending.get(approval_id)
        if rec is None or rec["closed_seq"] is not None:
            return False
        if filled_qty == 0:  # nada mudou na carteira
            self._drop(approval_id, rec)
        else:  # segura até a foto refletir o fill; senão a posição velha deixaria vender além do que há
            rec["closed_seq"] = seq if seq is not None else 0
        self._save_or_halt()
        self._release_applied()
        return True

    def on_fill(self, approval_id: str, qty: D, seq: int) -> bool:
        """Guarda o fill com o seq dele: só desconta da venda aberta quando a foto o alcança. Na compra
        a guarda não muda nada: a reserva fica inteira até fechar (conservador). False se o id é desconhecido."""
        rec = self.pending.get(approval_id)
        if rec is None:
            return False
        rec["fills"].append((seq, qty))
        self._save_or_halt()
        return True

    def release_orphans(self, logged_approvals: set) -> list[str]:
        """Pendência gravada em disco cuja aprovação nunca chegou ao log (queda entre gravar e publicar):
        nunca foi enviada, então a reserva e o trade voltam."""
        orphans = [k for k in self.pending if k not in logged_approvals]
        for k in orphans:
            self._drop(k, self.pending[k])
        if orphans:
            self._save_or_halt()
        return orphans

    def clear_halt(self, who: str, reason: str, reconciled: bool = False):
        """Solta as travas (registrado antes) e passa a ignorar fotos até chegar a que reflete o pedido.
        Trava de integridade só sai com a corretora conferida (`reconciled`): quem confere é o Execution."""
        if self.halts.integrity() and not reconciled:
            raise ReconciliationRequired("halt de integridade ativo: reconcilie com a corretora antes")
        cleared = self.halts.clear(who, reason)  # se falhar, nada foi limpo
        self.view = None
        self._awaiting_reset = uuid.uuid4().hex
        return cleared, self._awaiting_reset

    # --- decisão --------------------------------------------------------------------------

    def _invalid_view(self) -> dict | None:
        v = self.view
        vals = {k: _dec(v.get(k)) for k in ("equity", "peak_equity", "daily_pnl", "weekly_pnl")}
        bad = {k: str(v.get(k)) for k, d in vals.items() if d is None}
        if bad:
            return bad
        if vals["equity"] <= 0:
            return {"equity": str(v["equity"])}
        if vals["peak_equity"] < vals["equity"]:
            return {"peak_equity": str(v["peak_equity"]), "equity": str(v["equity"])}
        return None

    def decide(self, sig: dict) -> tuple[str, dict]:
        raw = sig["payload"]
        p = raw if isinstance(raw, dict) else {}
        side, ticker = p.get("side"), p.get("ticker")
        self._roll_day()

        def veto(rule: str, values: dict | None = None):
            return "vetoed", {"rule": rule, "values": values or {}, "ticker": ticker,
                              "side": side, "bar_id": p.get("bar_id"),
                              "filtros_inativos": FILTROS_INATIVOS}

        # 1. entrada inválida
        if not isinstance(raw, dict) or side not in ("buy", "sell") or not isinstance(ticker, str):
            return veto("sinal_invalido", {"side": str(side), "ticker": str(ticker),
                                           "payload": type(raw).__name__})
        if side == "buy":
            if ticker not in self.cfg.allowed_tickers:
                return veto("ticker_fora_da_lista", {"ticker": str(ticker)})
            weight, price = _dec(p.get("target_weight")), _dec(p.get("ref_price"))
            if weight is None or price is None or weight <= 0 or price <= 0:
                return veto("sinal_invalido", {"target_weight": str(p.get("target_weight")),
                                               "ref_price": str(p.get("ref_price"))})
        else:  # sair tem de ser possível mesmo se o ticker saiu da lista: vale a posição
            qtd = _dec(p.get("qtd"))
            if qtd is None or qtd <= 0:
                return veto("sell_excede", {"qtd": str(p.get("qtd"))})
        if self.view is None:
            return veto("sem_estado")
        today = self.clock.ny_date().isoformat()
        if self.view.get("session_date") != today:  # foto de outro dia: não decide por ela
            return veto("visao_velha", {"foto": str(self.view.get("session_date")), "hoje": today})
        bad = self._invalid_view()
        if bad:
            self._halt("estado_invalido", INTEGRITY, bad)
            return veto("estado_invalido", bad)

        # 2. travas
        if self.kill_path.exists():
            self._halt(KILL_SWITCH, LIMIT, {"arquivo": self.kill_path.name})
        v = self.view
        equity, peak = D(str(v["equity"])), D(str(v["peak_equity"]))
        state = RiskState(equity=float(equity), peak_equity=float(peak),
                          daily_pnl=float(D(str(v["daily_pnl"]))),
                          weekly_pnl=float(D(str(v["weekly_pnl"]))),
                          consecutive_losses=int(v["consecutive_losses"]),
                          trades_today=self.trades_today, last_trade_at=None, open_positions={})
        halt = global_halt(state, self.cfg.base)
        if halt is not None and halt.rule != TRADES:
            self._halt(halt.rule, LIMIT, {"value": halt.value}, _SCOPE.get(halt.rule))
        integ = self.halts.integrity()
        if integ:
            return veto(integ[0].rule, integ[0].detail)

        inputs = {"equity": str(v["equity"]), "peak_equity": str(v["peak_equity"]),
                  "daily_pnl": str(v["daily_pnl"]), "weekly_pnl": str(v["weekly_pnl"]),
                  "consecutive_losses": int(v["consecutive_losses"]),
                  "trades_today": self.trades_today}
        positions = v.get("positions", {})
        if side == "sell":
            return self._sell(sig, qtd, positions, inputs, veto)

        limit = self.halts.limit()
        if limit:
            return veto(limit[0].rule, limit[0].detail)
        if ticker in positions or any(r["ticker"] == ticker and r["side"] == "buy"
                                      for r in self.pending.values()):
            return veto("posicao_existente", {"ticker": ticker})
        if halt is not None and halt.rule == TRADES:
            return veto("trades_day", {"trades_today": self.trades_today,
                                       "limite": self.cfg.base.max_trades_per_day})

        # 3. dimensionamento: corta no teto (não veta), arredonda a quantidade para baixo
        factor = drawdown_factor(float(equity), float(peak), self.cfg.base.max_drawdown)
        requested = weight * D(repr(factor))
        final = min(requested, D(repr(self.cfg.base.max_position_weight)))
        qty = (equity * final / price).to_integral_value(rounding=ROUND_FLOOR)
        if qty <= 0:
            return veto("qty_zero", {"equity": str(equity), "peso": str(final), "preco": str(price)})
        reserve = qty * price
        held = sum((D(str(r["qty"])) * D(str(r["mark"])) for r in positions.values()), D(0))
        pending_buys = sum((r["reserve"] for r in self.pending.values() if r["side"] == "buy"), D(0))
        total, cap = held + pending_buys + reserve, D(repr(self.cfg.max_total_exposure)) * equity
        if total > cap:
            return veto("exposicao_total", {"total": str(total), "limite": str(cap)})

        approval_id = f"{ticker}:{p.get('bar_id')}:buy:{sig['seq']}"
        return self._commit(approval_id, "buy", ticker, qty, price, reserve, inputs, veto, {
            "weight_requested": str(requested), "weight_final": str(final),
            "cut": final < requested, "drawdown_factor": repr(factor)})

    def _sell(self, sig, qtd, positions, inputs, veto):
        ticker, p = sig["payload"]["ticker"], sig["payload"]
        pos = positions.get(ticker)
        held = D(str(pos["qty"])) if pos else D(0)
        open_sells = sum((self._open_sell_qty(r) for r in self.pending.values()
                          if r["side"] == "sell" and r["ticker"] == ticker), D(0))
        if qtd > held - open_sells:
            return veto("sell_excede", {"qtd": str(qtd), "posicao": str(held),
                                        "vendas_abertas": str(open_sells)})
        if pos is None or pos.get("mark") is None:
            return veto("sem_marca", {"ticker": ticker})
        approval_id = f"{ticker}:{p.get('bar_id')}:sell:{sig['seq']}"
        return self._commit(approval_id, "sell", ticker, qtd, D(str(pos["mark"])), D(0), inputs,
                            veto, {})

    def _open_sell_qty(self, rec: dict) -> D:
        """O que falta vender: desconta só os fills que a foto da carteira já aplicou (seq <= applied_seq);
        descontar antes deixaria a posição velha vender além do que há."""
        applied = self.view.get("applied_seq", 0)
        done = sum((q for s, q in rec["fills"] if s <= applied), D(0))
        return max(D(0), rec["qty"] - done)

    def _commit(self, approval_id, side, ticker, qty, price, reserve, inputs, veto, extra):
        self.pending[approval_id] = {"side": side, "ticker": ticker, "qty": qty, "reserve": reserve,
                                     "day": self.trade_day, "closed_seq": None, "fills": []}
        if side == "buy":
            self.trades_today += 1
        try:
            self._save()  # em arquivo antes de aprovar
        except StateWriteError:
            del self.pending[approval_id]
            if side == "buy":
                self.trades_today -= 1
            self._halt(STATE_WRITE_FAILED, INTEGRITY)
            return veto(STATE_WRITE_FAILED)
        return "approved", {"approval_id": approval_id, "side": side, "ticker": ticker,
                            "qtd": str(qty), "ref_price": str(price), "reserve": str(reserve),
                            "inputs": inputs, "filtros_inativos": FILTROS_INATIVOS, **extra}


class RiskAgent:
    def __init__(self, risk: Risk, bus):
        self.risk, self.bus = risk, bus
        self.pub = bus.register("risk", self.handle, critical=True,
                                subscribes=[SIGNAL_PROPOSED, PORTFOLIO_UPDATED, ORDER_CLOSED, FILL])

    async def handle(self, ev: dict) -> None:
        p = ev["payload"]
        if ev["type"] == PORTFOLIO_UPDATED:
            self.risk.on_view(p)
        elif ev["type"] == ORDER_CLOSED:
            qty = D(str(p["filled_qty"]))
            if not self.risk.on_order_closed(p["approval_id"], qty, ev["seq"]):
                self._ignored(ev, ORDER_CLOSED, qty)
        elif ev["type"] == FILL:
            qty = D(str(p["qty"]))
            if not self.risk.on_fill(p["approval_id"], qty, ev["seq"]):
                self._ignored(ev, FILL, qty)
        else:
            kind, payload = self.risk.decide(ev)
            try:
                self.pub.publish(ORDER_APPROVED if kind == "approved" else ORDER_VETOED, payload,
                                 ev["correlation_id"], ev["seq"])
            except Exception:
                if kind == "approved":  # a aprovação não saiu: a reserva não pode ficar órfã
                    self.risk.on_order_closed(payload["approval_id"], D(0))
                raise

    def _ignored(self, ev: dict, kind: str, qty: D) -> None:
        """Id desconhecido (ou fechamento repetido): não muda nada, mas fica no log."""
        p = ev["payload"]
        self.pub.publish(ORDER_EVENT_IGNORED, {
            "event": kind, "approval_id": p["approval_id"], "qty": str(qty), "status": p.get("status")},
            ev["correlation_id"], ev["seq"])

    def release_orphans(self) -> list[str]:
        """Na partida: devolve a reserva de toda pendência cuja aprovação não está no log."""
        logged = {r["payload"].get("approval_id") for r in read_log(self.bus.audit.path)
                  if r["type"] == ORDER_APPROVED}
        released = self.risk.release_orphans(logged)
        for approval_id in released:
            self.pub.publish(PENDING_ORPHANED, {"approval_id": approval_id},
                             correlation_id=f"orphan:{approval_id}")
        return released

    def clear_halt(self, who: str, reason: str, reconciled: bool = False):
        """Solta as travas e pede ao Portfolio o que cada uma exige (drawdown: refazer o pico;
        perdas seguidas: zerar a sequência). Só pode ser chamado de dentro do loop de eventos."""
        cleared, reset_id = self.risk.clear_halt(who, reason, reconciled)
        rules = {r.rule for r in cleared}
        self.pub.publish(RISK_RESET_REQUESTED, {
            "peak": DRAWDOWN in rules, "losses": CONSECUTIVE_LOSSES in rules,
            "who": who, "reason": reason, "reset_id": reset_id})
        return cleared
