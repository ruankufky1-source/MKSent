import json
from datetime import date
from decimal import Decimal as D

import pytest

from sentinela.strategy.risk_controller import RiskConfig
from sentinela.trading import store
from sentinela.trading.halts import HaltStore
from sentinela.trading.risk import AgentRiskConfig, Risk
from sentinela.trading.store import StateWriteError

TICKERS = frozenset({"AAA", "BBB", "CCC", "MSFT"})


def base_cfg(**kw):
    d = dict(max_daily_loss=-0.03, max_weekly_loss=-0.06, max_drawdown=-0.15,
             max_consecutive_losses=4, max_trades_per_day=5, min_minutes_between_trades=1,
             max_position_weight=0.10, earnings_blackout_days=2)
    d.update(kw)
    return RiskConfig(**d)


def cfg(exposure=0.5, **kw):
    return AgentRiskConfig(base_cfg(**kw), max_total_exposure=exposure, allowed_tickers=TICKERS)


def view(equity="10000", peak=None, daily="0", weekly="0", losses=0, positions=None,
         day=None, applied_seq=0, reset_id=None):
    # day=None: o Env preenche com a data do relógio (foto "de hoje"); passe `day` para foto velha
    return {"session_date": day, "cash": equity, "equity": equity,
            "peak_equity": peak or equity, "daily_pnl": daily, "weekly_pnl": weekly,
            "consecutive_losses": losses, "positions": positions or {},
            "applied_seq": applied_seq, "reset_id": reset_id}


def pos(qty, mark="100", avg="100"):
    return {"qty": str(qty), "avg": avg, "mark": mark}


def buy(ticker="AAA", weight="0.05", price="50", bar="b1", seq=1):
    return {"seq": seq, "correlation_id": f"c{seq}",
            "payload": {"side": "buy", "ticker": ticker, "target_weight": weight,
                        "ref_price": price, "bar_id": bar}}


def sell(ticker="AAA", qtd="6", bar="b1", seq=1):
    return {"seq": seq, "correlation_id": f"c{seq}",
            "payload": {"side": "sell", "ticker": ticker, "qtd": qtd, "bar_id": bar}}


class Env:
    def __init__(self, tmp_path, clock, config=None, v=None, first_run=True):
        self.tmp, self.clock = tmp_path, clock
        self.cfg = config or cfg()
        self.halts = HaltStore(tmp_path / "halts.json", clock, first_run=first_run)
        self.kill = tmp_path / "PARAR"
        self.risk = Risk(self.cfg, self.halts, tmp_path / "risk_state.json", clock, self.kill,
                         first_run=first_run)
        if v is not False:
            self.set_view(v or view())

    def set_view(self, v):
        v = dict(v)
        if v.get("session_date") is None:
            v["session_date"] = self.clock.ny_date().isoformat()
        self.risk.on_view(v)

    def restart(self, v=None):
        self.halts = HaltStore(self.tmp / "halts.json", self.clock)
        self.risk = Risk(self.cfg, self.halts, self.tmp / "risk_state.json", self.clock, self.kill)
        self.set_view(v or view())

    def decide(self, sig):
        return self.risk.decide(sig)

    def rules(self):
        return sorted(r.rule for r in self.halts.active())


def ok(res):
    assert res[0] == "approved", res
    return res[1]


def vetoed(res, rule):
    assert res[0] == "vetoed", res
    assert res[1]["rule"] == rule, res
    return res[1]


# --- configuração ---------------------------------------------------------------------------

@pytest.mark.parametrize("kw", [
    dict(max_drawdown=0.0), dict(max_drawdown=-15), dict(max_drawdown=0.15),
    dict(max_drawdown=float("nan")), dict(max_daily_loss=0.03), dict(max_weekly_loss=-6),
    dict(max_position_weight=0.0), dict(max_position_weight=1.5),
    dict(max_consecutive_losses=0), dict(max_trades_per_day=0),
])
def test_config_rejects_bad_base_values(kw):
    with pytest.raises(ValueError):
        cfg(**kw)


@pytest.mark.parametrize("exposure", [0.0, -0.1, 1.5, float("nan")])
def test_config_rejects_bad_total_exposure(exposure):
    with pytest.raises(ValueError):
        cfg(exposure=exposure)


def test_config_accepts_boundaries_and_rejects_empty_ticker_list():
    cfg(exposure=1.0, max_position_weight=1.0)
    with pytest.raises(ValueError):
        AgentRiskConfig(base_cfg(), max_total_exposure=0.5, allowed_tickers=frozenset())


# --- entrada inválida -----------------------------------------------------------------------

@pytest.mark.parametrize("field", ["equity", "peak_equity", "daily_pnl", "weekly_pnl"])
@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_nonfinite_view_field_is_integrity_halt_never_exception(tmp_path, clock, field, bad):
    e = Env(tmp_path, clock)
    ok(e.decide(buy(seq=1)))  # controle positivo: estado saudável aprova
    v = view()
    v[field] = bad
    e.set_view(v)
    vetoed(e.decide(buy(ticker="BBB", seq=2)), "estado_invalido")
    assert "estado_invalido" in e.rules() and e.halts.integrity()


@pytest.mark.parametrize("v", [view(equity="0"), view(equity="-5"), view(equity="10000", peak="9000")])
def test_equity_not_positive_or_peak_below_equity_is_integrity_halt(tmp_path, clock, v):
    e = Env(tmp_path, clock, v=v)
    vetoed(e.decide(buy()), "estado_invalido")
    assert "estado_invalido" in e.rules()


def test_no_view_yet_is_a_veto_without_exception(tmp_path, clock):
    e = Env(tmp_path, clock, v=False)
    vetoed(e.decide(buy()), "sem_estado")


@pytest.mark.parametrize("weight, price", [("0", "50"), ("-0.05", "50"), ("nan", "50"),
                                           ("0.05", "0"), ("0.05", "-1"), ("0.05", "nan"),
                                           ("inf", "50"), ("0.05", "inf")])
def test_invalid_buy_signal_is_vetoed_without_halt(tmp_path, clock, weight, price):
    e = Env(tmp_path, clock)
    vetoed(e.decide(buy(weight=weight, price=price)), "sinal_invalido")
    assert e.halts.active() == []
    ok(e.decide(buy(seq=2)))  # controle positivo


@pytest.mark.parametrize("side", ["short", "hold", "", None, 1])
def test_unknown_side_is_vetoed_as_invalid_signal(tmp_path, clock, side):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    sig = sell(qtd="1")
    sig["payload"]["side"] = side
    vetoed(e.decide(sig), "sinal_invalido")
    assert e.halts.active() == []
    ok(e.decide(sell(qtd="1", seq=2)))  # controle positivo


def test_buy_of_ticker_outside_the_list_is_vetoed(tmp_path, clock):
    e = Env(tmp_path, clock)
    vetoed(e.decide(buy(ticker="ZZZ")), "ticker_fora_da_lista")
    assert e.halts.active() == []


def test_selling_a_held_ticker_that_left_the_list_is_allowed(tmp_path, clock):
    # a lista pode mudar com posição aberta: sair tem de continuar possível
    e = Env(tmp_path, clock, v=view(positions={"ZZZ": pos(10)}))
    ok(e.decide(sell(ticker="ZZZ", qtd="6")))
    vetoed(e.decide(sell(ticker="YYY", qtd="1", seq=2)), "sell_excede")  # sem posição: nunca short


@pytest.mark.parametrize("payload", [None, [], "texto", 7])
def test_payload_that_is_not_a_dict_is_an_invalid_signal_not_a_crash(tmp_path, clock, payload):
    e = Env(tmp_path, clock)
    vetoed(e.decide({"seq": 1, "correlation_id": "c1", "payload": payload}), "sinal_invalido")
    assert e.halts.active() == []
    ok(e.decide(buy(seq=2)))  # controle positivo


@pytest.mark.parametrize("ticker", [None, 5, ["AAA"], {"a": 1}])
@pytest.mark.parametrize("make", [buy, sell])
def test_ticker_of_the_wrong_type_is_an_invalid_signal_not_a_crash(tmp_path, clock, ticker, make):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    sig = make()
    sig["payload"]["ticker"] = ticker
    vetoed(e.decide(sig), "sinal_invalido")
    assert e.halts.active() == []
    ok(e.decide(make(ticker="BBB" if make is buy else "AAA", seq=2)))  # controle positivo


def test_stale_view_after_day_roll_is_vetoed_and_does_not_rearm_an_expired_halt(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(daily="-0.035"))
    vetoed(e.decide(buy()), "daily_loss")
    clock.set_date(date(2026, 7, 21))  # virou o dia; a foto ainda é a de ontem
    vetoed(e.decide(buy(seq=2)), "visao_velha")
    assert e.halts.active() == []  # a trava de ontem expirou e não foi recriada
    e.set_view(view(daily="0"))
    ok(e.decide(buy(seq=3)))


def test_stale_view_after_week_roll_is_vetoed(tmp_path, clock):
    clock.set_date(date(2026, 7, 24))  # sexta
    e = Env(tmp_path, clock, v=view(weekly="-0.07"))
    vetoed(e.decide(buy()), "weekly_loss")
    clock.set_date(date(2026, 7, 27))  # segunda, semana ISO nova
    vetoed(e.decide(buy(seq=2)), "visao_velha")
    assert e.halts.active() == []
    e.set_view(view())
    ok(e.decide(buy(seq=3)))


def test_view_without_a_session_date_is_stale(tmp_path, clock):
    e = Env(tmp_path, clock, v=False)
    v = view()
    v["session_date"] = None
    e.risk.on_view(v)  # direto, sem o Env preencher a data
    vetoed(e.decide(buy()), "visao_velha")


@pytest.mark.parametrize("qtd", ["nan", "inf", "-inf", "0", "-1"])
def test_invalid_sell_quantity_is_sell_excede(tmp_path, clock, qtd):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    vetoed(e.decide(sell(qtd=qtd)), "sell_excede")
    ok(e.decide(sell(qtd="6", seq=2)))  # controle positivo


# --- sizing, reserva e liberação -------------------------------------------------------------

@pytest.mark.parametrize("equity, peak, weight, price, qty, reserve, cut", [
    ("10000", "10000", "0.05", "50", 10, 500, False),
    ("9250", "10000", "0.05", "50", 4, 200, False),     # fator 0,5: 2,5% = 231,25 -> 4 ações
    ("10000", "10000", "0.20", "50", 20, 1000, True),   # pedido 20% é cortado no teto de 10%
])
def test_sizing_examples(tmp_path, clock, equity, peak, weight, price, qty, reserve, cut):
    e = Env(tmp_path, clock, v=view(equity=equity, peak=peak))
    a = ok(e.decide(buy(weight=weight, price=price)))
    assert D(a["qtd"]) == qty and D(a["reserve"]) == reserve
    assert a["cut"] is cut and a["side"] == "buy" and D(a["ref_price"]) == D(price)
    assert a["filtros_inativos"] == ["earnings_blackout", "min_interval"]


def test_allocation_smaller_than_one_share_is_qty_zero(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(equity="1000"))
    v = vetoed(e.decide(buy(weight="0.05", price="60")), "qty_zero")
    assert v["values"]["equity"] == "1000"


def test_reservation_is_on_disk_before_approval_returns(tmp_path, clock):
    e = Env(tmp_path, clock)
    a = ok(e.decide(buy()))
    state = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    assert state["trades_today"] == 1
    assert state["pending"][a["approval_id"]]["reserve"] == "500"


def test_state_write_failure_vetoes_and_halts_with_positive_control(tmp_path, clock, monkeypatch):
    e = Env(tmp_path, clock)
    real = store.atomic_write_json

    def boom(path, obj):
        raise StateWriteError("disco cheio")

    monkeypatch.setattr(store, "atomic_write_json", boom)
    vetoed(e.decide(buy()), "state_write_failed")
    assert "state_write_failed" in e.rules()
    monkeypatch.setattr(store, "atomic_write_json", real)
    e.halts.clear("t", "t")
    a = ok(e.decide(buy(seq=2)))  # a reserva da tentativa falha não ficou
    assert D(a["reserve"]) == 500
    state = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    assert state["trades_today"] == 1 and len(state["pending"]) == 1


def test_trades_day_is_a_veto_not_a_halt_and_resets_next_day(tmp_path, clock):
    e = Env(tmp_path, clock, config=cfg(max_trades_per_day=1))
    ok(e.decide(buy(ticker="AAA", seq=1)))
    vetoed(e.decide(buy(ticker="BBB", seq=2)), "trades_day")
    assert e.halts.active() == []  # nenhum halt gravado
    assert json.loads((e.halts.path).read_text(encoding="utf-8"))["halts"] == []
    clock.set_date(date(2026, 7, 21))
    e.set_view(view())
    ok(e.decide(buy(ticker="BBB", seq=3)))


def test_order_closed_without_fill_frees_reserve_and_trade(tmp_path, clock):
    e = Env(tmp_path, clock, config=cfg(max_trades_per_day=1))
    a = ok(e.decide(buy(ticker="AAA", seq=1)))
    e.risk.on_order_closed(a["approval_id"], D("0"))
    b = ok(e.decide(buy(ticker="BBB", seq=2)))  # o trade e a reserva foram liberados
    assert D(b["reserve"]) == 500
    state = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    assert list(state["pending"]) == [b["approval_id"]]


def test_order_closed_with_partial_fill_consumes_the_trade_and_frees_exposure_after_portfolio(
        tmp_path, clock):
    e = Env(tmp_path, clock, config=cfg(max_trades_per_day=1))
    a = ok(e.decide(buy(ticker="AAA", seq=1)))
    e.risk.on_order_closed(a["approval_id"], D("3"), seq=10)
    vetoed(e.decide(buy(ticker="BBB", seq=2)), "trades_day")
    state = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    assert list(state["pending"]) == [a["approval_id"]]   # ainda segura até a foto refletir o fill
    e.set_view(view(positions={"AAA": pos(3)}, applied_seq=10))
    state = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    assert state["pending"] == {} and state["trades_today"] == 1


def test_closed_sell_keeps_weighing_until_the_portfolio_applied_the_fill(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    e.risk.on_order_closed(a["approval_id"], D("6"), seq=50)  # fechou, mas a foto ainda mostra 10
    vetoed(e.decide(sell(qtd="7", seq=2)), "sell_excede")    # sem esta regra viraria short
    e.set_view(view(positions={"AAA": pos(10)}, applied_seq=49))   # foto ainda anterior ao fechamento
    vetoed(e.decide(sell(qtd="7", seq=3)), "sell_excede")
    e.set_view(view(positions={"AAA": pos(4)}, applied_seq=50))    # agora o fill está na foto
    vetoed(e.decide(sell(qtd="5", seq=4)), "sell_excede")
    ok(e.decide(sell(qtd="4", seq=5)))


def test_closed_order_is_released_at_once_if_the_portfolio_is_already_past_the_closing(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}, applied_seq=100))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    e.risk.on_order_closed(a["approval_id"], D("6"), seq=50)  # a foto já aplicou até o evento 100
    state = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    assert state["pending"] == {}


def test_closed_buy_keeps_blocking_the_ticker_and_exposure_until_applied(tmp_path, clock):
    e = Env(tmp_path, clock)
    a = ok(e.decide(buy(ticker="AAA", seq=1)))
    e.risk.on_order_closed(a["approval_id"], D("10"), seq=60)
    vetoed(e.decide(buy(ticker="AAA", seq=2)), "posicao_existente")   # foto ainda sem a posição
    e.set_view(view(positions={"AAA": pos(10, mark="50")}, applied_seq=60))
    vetoed(e.decide(buy(ticker="AAA", seq=3)), "posicao_existente")   # agora pela posição
    state = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    assert state["pending"] == {}


def test_closed_order_waiting_for_the_portfolio_survives_restart(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    a = ok(e.decide(sell(qtd="6", seq=1)))
    e.risk.on_order_closed(a["approval_id"], D("6"), seq=70)
    e.restart(v=view(positions={"AAA": pos(10)}, applied_seq=0))
    vetoed(e.decide(sell(qtd="7", seq=2)), "sell_excede")
    e.set_view(view(positions={"AAA": pos(4)}, applied_seq=70))
    ok(e.decide(sell(qtd="4", seq=3)))


def test_risk_state_survives_restart_trades_and_pending(tmp_path, clock):
    e = Env(tmp_path, clock, config=cfg(max_trades_per_day=1))
    ok(e.decide(buy(ticker="AAA", seq=1)))
    e.restart()
    vetoed(e.decide(buy(ticker="BBB", seq=2)), "trades_day")        # contagem do dia voltou
    vetoed(e.decide(buy(ticker="AAA", seq=3)), "posicao_existente")  # reserva voltou
    state = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    assert state["trades_today"] == 1 and len(state["pending"]) == 1


def test_existing_position_or_pending_buy_of_same_ticker_is_vetoed(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(8, mark="100")}))  # 8% em carteira
    vetoed(e.decide(buy(ticker="AAA", weight="0.05")), "posicao_existente")
    ok(e.decide(buy(ticker="BBB", seq=2)))
    vetoed(e.decide(buy(ticker="BBB", seq=3)), "posicao_existente")  # compra pendente


def test_total_exposure_boundary_exact_passes(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"MSFT": pos(40, mark="100")}))  # 4.000 em carteira
    ok(e.decide(buy(ticker="AAA", weight="0.05", price="50", seq=1)))   # pendente 500
    ok(e.decide(buy(ticker="BBB", weight="0.05", price="50", seq=2)))   # 4.000+500+500 = 5.000 = limite


def test_total_exposure_one_step_above_is_vetoed(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"MSFT": pos(40, mark="100")}))
    ok(e.decide(buy(ticker="AAA", weight="0.05", price="50", seq=1)))   # pendente 500
    v = vetoed(e.decide(buy(ticker="BBB", weight="0.055", price="55", seq=2)), "exposicao_total")
    assert D(v["values"]["total"]) == 5050 and D(v["values"]["limite"]) == 5000


# --- halts de limite ------------------------------------------------------------------------

def test_daily_loss_latches_the_day_and_expires_next_day(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(daily="-0.03"))
    vetoed(e.decide(buy()), "daily_loss")
    e.set_view(view(daily="0"))  # recuperou no meio do dia: continua travado
    vetoed(e.decide(buy(seq=2)), "daily_loss")
    clock.set_date(date(2026, 7, 21))
    e.set_view(view(daily="0"))
    ok(e.decide(buy(seq=3)))


def test_weekly_loss_lasts_the_iso_week(tmp_path, clock):
    clock.set_date(date(2026, 7, 22))  # quarta
    e = Env(tmp_path, clock, v=view(weekly="-0.07"))
    vetoed(e.decide(buy()), "weekly_loss")
    clock.set_date(date(2026, 7, 24))  # sexta
    e.set_view(view())
    vetoed(e.decide(buy(seq=2)), "weekly_loss")
    clock.set_date(date(2026, 7, 27))  # segunda seguinte
    e.set_view(view())
    ok(e.decide(buy(seq=3)))


def test_after_clear_the_old_view_is_ignored_until_the_reset_view_arrives(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(equity="8000", peak="10000"))
    vetoed(e.decide(buy()), "drawdown")
    cleared, reset_id = e.risk.clear_halt("ruan", "conferi")
    assert [r.rule for r in cleared] == ["drawdown"]
    e.set_view(view(equity="8000", peak="10000"))           # foto velha chegando atrasada
    vetoed(e.decide(buy(seq=2)), "sem_estado")              # nada de rearmar o drawdown
    assert e.halts.active() == []
    e.set_view(view(equity="8000", peak="8000", reset_id=reset_id))
    ok(e.decide(buy(seq=3)))


@pytest.mark.parametrize("v, rule", [
    (view(equity="8000", peak="10000"), "drawdown"),
    (view(losses=4), "consecutive_losses"),
])
def test_drawdown_and_consecutive_losses_survive_restart_until_clear(tmp_path, clock, v, rule):
    e = Env(tmp_path, clock, v=v)
    vetoed(e.decide(buy()), rule)
    e.restart(v=view())  # reiniciou com estado já saudável: continua em halt
    vetoed(e.decide(buy(seq=2)), rule)
    e.halts.clear("ruan", "conferi")
    ok(e.decide(buy(seq=3)))


def test_kill_file_halts_and_removing_it_does_not_release(tmp_path, clock):
    e = Env(tmp_path, clock)
    e.kill.write_text("parar", encoding="utf-8")
    vetoed(e.decide(buy()), "kill_switch")
    e.kill.unlink()
    vetoed(e.decide(buy(seq=2)), "kill_switch")  # travado até clear_halt
    e.halts.clear("ruan", "ok")
    ok(e.decide(buy(seq=3)))
    e.kill.write_text("parar", encoding="utf-8")  # arquivo ainda presente: trava de novo
    vetoed(e.decide(buy(ticker="BBB", seq=4)), "kill_switch")


def test_missing_or_corrupt_risk_state_is_integrity_halt(tmp_path, clock):
    e = Env(tmp_path, clock)
    ok(e.decide(buy()))
    (tmp_path / "risk_state.json").unlink()
    e.restart()
    vetoed(e.decide(buy(ticker="BBB", seq=2)), "risk_estado_ausente")
    (tmp_path / "risk_state.json").write_text("{lixo", encoding="utf-8")
    e.halts.clear("t", "t")
    e.restart()
    vetoed(e.decide(buy(ticker="BBB", seq=3)), "risk_estado_corrompido")


def test_halts_state_missing_blocks_everything(tmp_path, clock):
    e = Env(tmp_path, clock)
    (tmp_path / "halts.json").unlink()
    e.restart()
    vetoed(e.decide(buy()), "estado_ausente")


# --- saídas ---------------------------------------------------------------------------------

def test_limit_halt_blocks_entry_but_reducing_sell_passes_with_borders(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(equity="8000", peak="10000", positions={"AAA": pos(10)}))
    vetoed(e.decide(buy(ticker="BBB")), "drawdown")
    ok(e.decide(sell(qtd="4", seq=2)))               # vendas abertas: 4
    ok(e.decide(sell(qtd="6", seq=3)))               # 10 - 4 = 6: passa
    vetoed(e.decide(sell(qtd="1", seq=4)), "sell_excede")


def test_sell_of_seven_with_four_open_is_vetoed(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    ok(e.decide(sell(qtd="4", seq=1)))
    vetoed(e.decide(sell(qtd="7", seq=2)), "sell_excede")
    ok(e.decide(sell(qtd="6", seq=3)))


def test_sell_without_position_is_never_a_short(tmp_path, clock):
    e = Env(tmp_path, clock)
    vetoed(e.decide(sell(qtd="1")), "sell_excede")


def test_sell_passes_when_trades_per_day_limit_is_reached_and_is_not_counted(tmp_path, clock):
    e = Env(tmp_path, clock, config=cfg(max_trades_per_day=1),
            v=view(positions={"MSFT": pos(10)}))
    ok(e.decide(buy(ticker="AAA", seq=1)))
    vetoed(e.decide(buy(ticker="BBB", seq=2)), "trades_day")
    ok(e.decide(sell(ticker="MSFT", qtd="6", seq=3)))
    state = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    assert state["trades_today"] == 1


def test_integrity_halt_blocks_even_the_exit(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10)}))
    ok(e.decide(sell(qtd="6", seq=1)))  # controle positivo
    e.halts.raise_("fila_cheia", "integrity")
    vetoed(e.decide(sell(qtd="1", seq=2)), "fila_cheia")
    vetoed(e.decide(buy(ticker="BBB", seq=3)), "fila_cheia")


def test_sell_without_mark_is_sem_marca(tmp_path, clock):
    v = view(positions={"AAA": {"qty": "10", "avg": "100"}})  # foto sem marca
    e = Env(tmp_path, clock, v=v)
    vetoed(e.decide(sell(qtd="1")), "sem_marca")


def test_sell_approval_uses_last_mark_as_ref_price(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(positions={"AAA": pos(10, mark="123.45")}))
    a = ok(e.decide(sell(qtd="2")))
    assert a["side"] == "sell" and D(a["qtd"]) == 2 and D(a["ref_price"]) == D("123.45")


# --- ordem das regras -----------------------------------------------------------------------

def test_rule_order_when_several_fire_together(tmp_path, clock):
    # compra de AAA já em carteira + exposição estourada + trades no limite: vale posicao_existente
    e = Env(tmp_path, clock, config=cfg(max_trades_per_day=1),
            v=view(positions={"AAA": pos(8), "MSFT": pos(10)}))
    ok(e.decide(buy(ticker="CCC", seq=1)))  # consome o trade do dia
    vetoed(e.decide(buy(ticker="AAA", seq=2)), "posicao_existente")  # também seria trades_day
    # trades_day vem antes de qty_zero e de exposicao_total
    vetoed(e.decide(buy(ticker="BBB", weight="0.001", price="5000", seq=3)), "trades_day")


def test_limit_halt_wins_over_position_rule_and_integrity_wins_over_limit(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(daily="-0.04", positions={"AAA": pos(8)}))
    vetoed(e.decide(buy(ticker="AAA")), "daily_loss")
    e.halts.raise_("fila_cheia", "integrity")
    vetoed(e.decide(buy(ticker="AAA", seq=2)), "fila_cheia")


def test_invalid_signal_wins_over_halts(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(daily="-0.04"))
    e.halts.raise_("fila_cheia", "integrity")
    vetoed(e.decide(buy(weight="nan")), "sinal_invalido")


# --- imutabilidade e log de inputs ----------------------------------------------------------

def test_risk_keeps_its_own_copy_of_the_view(tmp_path, clock):
    e = Env(tmp_path, clock)
    v = view(day=clock.ny_date().isoformat())
    e.risk.on_view(v)
    v["equity"] = "nan"
    v["positions"]["AAA"] = pos(999)
    ok(e.decide(buy()))


def test_approval_and_veto_carry_inputs_and_values(tmp_path, clock):
    e = Env(tmp_path, clock, v=view(equity="9250", peak="10000"))
    a = ok(e.decide(buy()))
    assert a["inputs"]["equity"] == "9250" and a["inputs"]["peak_equity"] == "10000"
    assert a["inputs"]["trades_today"] == 0  # valor visto na decisão, antes de reservar
    assert a["inputs"]["daily_pnl"] == "0" and a["inputs"]["weekly_pnl"] == "0"
