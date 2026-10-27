import asyncio
from decimal import Decimal as D

import pytest

from sentinela.trading.broker import (
    BrokerError, BrokerOrder, BrokerTimeout, DuplicateOrderId, GuardConfig, GuardedBroker,
    GuardRejected, OrderRequest, TERMINAL,
)
from sentinela.trading.simbroker import SimBroker
from tests.trading.shared_loop import run


def req(cid="sm-1-AAA-buy", ticker="AAA", side="buy", qty="10", price="50"):
    return OrderRequest(cid, ticker, side, D(qty), D(price))


def guarded(sim=None, cap="500", max_qty="10", tickers=("AAA",)):
    sim = sim or SimBroker(seed=1, partial_prob=0)
    return sim, GuardedBroker(sim, GuardConfig(D(cap), D(max_qty), frozenset(tickers)))


# --- critério 1: GuardedBroker, independente do Risk ----------------------------------------

def test_guard_ceiling_exact_value_passes_one_cent_above_is_refused():
    sim, g = guarded()
    order = run(g.submit(req(qty="10", price="50.00")))   # 500,00 = o teto: passa
    assert order.client_order_id == "sm-1-AAA-buy" and sim.submit_calls == 1
    with pytest.raises(GuardRejected) as e:
        run(g.submit(req(cid="sm-2-AAA-buy", qty="10", price="50.01")))   # 500,10
    assert e.value.rule == "teto_de_valor"
    assert sim.submit_calls == 1   # a recusada nem chegou à corretora


def test_guard_max_qty_catches_a_wrong_price_that_the_value_ceiling_would_let_through():
    sim, g = guarded(cap="500", max_qty="10")
    run(g.submit(req(cid="a", qty="10", price="1")))   # 10 passa
    with pytest.raises(GuardRejected) as e:
        run(g.submit(req(cid="b", qty="11", price="1")))   # 11 × 1 = 11: o valor passaria
    assert e.value.rule == "qtd_acima_do_maximo"
    with pytest.raises(GuardRejected) as e:
        run(g.submit(req(cid="c", qty="50000", price="0.01")))   # preço errado: valor = 500, passaria
    assert e.value.rule == "qtd_acima_do_maximo" and sim.submit_calls == 1
    run(g.submit(req(cid="d", side="sell", qty="50", price="1")))   # venda não tem teto de quantidade
    assert sim.submit_calls == 2


def test_guard_ticker_list_applies_to_buys_only_and_is_its_own_list():
    sim, g = guarded(tickers=("AAA",))
    with pytest.raises(GuardRejected) as e:
        run(g.submit(req(cid="sm-3-BBB-buy", ticker="BBB")))
    assert e.value.rule == "ticker_fora_da_lista" and sim.submit_calls == 0
    run(g.submit(req(cid="sm-4-BBB-sell", ticker="BBB", side="sell", qty="30", price="100")))
    assert sim.submit_calls == 1   # venda de 3.000 de ticker fora da lista passa: sair tem de ser possível


@pytest.mark.parametrize("kw, rule", [
    (dict(qty="0"), "qtd_invalida"),
    (dict(qty="-1"), "qtd_invalida"),
    (dict(qty="NaN"), "qtd_invalida"),
    (dict(qty="Infinity"), "qtd_invalida"),
    (dict(qty="-Infinity"), "qtd_invalida"),
    (dict(price="0"), "preco_invalido"),
    (dict(price="NaN"), "preco_invalido"),
    (dict(price="Infinity"), "preco_invalido"),
    (dict(side="hold"), "lado_invalido"),
])
def test_guard_refuses_invalid_requests_before_the_broker(kw, rule):
    sim, g = guarded()
    with pytest.raises(GuardRejected) as e:
        run(g.submit(req(**kw)))
    assert e.value.rule == rule and sim.submit_calls == 0
    run(g.submit(req()))   # controle positivo: o pedido saudável passa
    assert sim.submit_calls == 1


@pytest.mark.parametrize("kw", [
    dict(max_order_value=D("0")), dict(max_order_value=D("-1")), dict(max_order_value=D("NaN")),
    dict(max_order_value=D("Infinity")), dict(max_qty=D("0")), dict(max_qty=D("NaN")),
    dict(max_qty=D("-3")), dict(allowed_tickers=frozenset()),
])
def test_guard_config_refuses_nonsense(kw):
    base = dict(max_order_value=D("500"), max_qty=D("10"), allowed_tickers=frozenset({"AAA"}))
    base.update(kw)
    with pytest.raises(ValueError):
        GuardConfig(**base)
    GuardConfig(D("500"), D("10"), frozenset({"AAA"}))   # controle positivo


def test_guard_passes_reads_through_unchanged():
    sim, g = guarded()
    run(g.submit(req()))
    assert run(g.get_order("sm-1-AAA-buy")).filled_qty == D("10")
    assert run(g.get_order("nao-existe")) is None
    assert run(g.positions()) == {"AAA": D("10")}
    assert run(g.open_orders()) == []


# --- contrato do corretor, vale para o SimBroker e depois para o AlpacaBroker ---------------

def test_broker_contract_submit_get_and_duplicate_id():
    sim = SimBroker(seed=1, partial_prob=0)
    o = run(sim.submit(req()))
    assert isinstance(o, BrokerOrder) and o.status == "filled" and o.filled_qty == D("10")
    assert o.filled_avg_price == D("50")
    assert run(sim.get_order(o.client_order_id)) == o
    with pytest.raises(DuplicateOrderId):
        run(sim.submit(req()))
    assert len(sim.orders) == 1   # a repetida não criou outra ordem
    assert sim.submit_calls == 2


def test_broker_contract_positions_follow_fills_and_sells_reduce():
    sim = SimBroker(seed=1, partial_prob=0)
    run(sim.submit(req(cid="a")))
    run(sim.submit(req(cid="b", side="sell", qty="4", price="60")))
    assert run(sim.positions()) == {"AAA": D("6")}
    run(sim.submit(req(cid="c", side="sell", qty="6", price="60")))
    assert run(sim.positions()) == {}   # posição zerada some


def test_broker_contract_open_orders_only_lists_non_terminal():
    sim = SimBroker(seed=1, partial_prob=0)
    sim.plan_next([("4", "100"), ("6", "101")])
    o = run(sim.submit(req(cid="a", price="100")))
    assert o.status == "partially_filled" and o.filled_qty == D("4")
    assert [x.client_order_id for x in run(sim.open_orders())] == ["a"]
    sim.advance()
    assert run(sim.get_order("a")).status == "filled" and run(sim.open_orders()) == []


def test_sim_scripted_fills_accumulate_quantity_and_average_price():
    sim = SimBroker(seed=1, partial_prob=0)
    sim.plan_next([("4", "100"), ("6", "101")])
    run(sim.submit(req(price="100")))
    sim.advance()
    o = run(sim.get_order("sm-1-AAA-buy"))
    assert o.filled_qty == D("10") and o.filled_avg_price == D("100.60")


def test_sim_cancel_keeps_what_was_filled_and_is_terminal():
    sim = SimBroker(seed=1, partial_prob=0)
    sim.plan_next([("4", "100"), ("6", "101")])
    run(sim.submit(req(price="100")))
    sim.cancel("sm-1-AAA-buy")
    o = run(sim.get_order("sm-1-AAA-buy"))
    assert o.status == "canceled" and o.filled_qty == D("4") and o.status in TERMINAL
    assert run(sim.positions()) == {"AAA": D("4")}
    sim.advance()
    assert run(sim.get_order("sm-1-AAA-buy")).filled_qty == D("4")   # cancelada não enche mais


# --- falhas injetadas -----------------------------------------------------------------------

def test_sim_timeout_fault_creates_no_order_and_lost_response_creates_it():
    sim = SimBroker(seed=1, partial_prob=0, stall_s=0.5)
    sim.inject("timeout", "lost_response")

    async def go():
        for cid in ("a", "b"):
            with pytest.raises(asyncio.TimeoutError):
                async with asyncio.timeout(0.05):
                    await sim.submit(req(cid=cid))

    run(go())
    assert run(sim.get_order("a")) is None                  # nunca chegou
    assert run(sim.get_order("b")).filled_qty == D("10")    # chegou, a resposta se perdeu
    assert sim.submit_calls == 2


def test_sim_reject_fault_returns_a_rejected_order_without_position():
    sim = SimBroker(seed=1, partial_prob=0)
    sim.inject("reject")
    o = run(sim.submit(req()))
    assert o.status == "rejected" and o.filled_qty == 0 and run(sim.positions()) == {}
    assert run(sim.submit(req(cid="b"))).status == "filled"   # a falha é de uma vez só


def test_sim_foreign_order_and_forced_positions_for_reconciliation_tests():
    sim = SimBroker(seed=1, partial_prob=0)
    sim.add_foreign_order("manual-123", "AAA", "buy", D("5"))
    sim.set_positions({"AAA": D("12")})
    assert [o.client_order_id for o in run(sim.open_orders())] == ["manual-123"]
    assert run(sim.positions()) == {"AAA": D("12")}


def test_broker_errors_share_a_base_class():
    assert issubclass(BrokerTimeout, BrokerError) and issubclass(DuplicateOrderId, BrokerError)


# --- critério 6: semente fixa dos 10% de fills parciais -------------------------------------

def _pattern(seed, n):
    sim = SimBroker(seed=seed)   # partial_prob padrão = 0,10
    out = []
    for i in range(n):
        o = run(sim.submit(req(cid=f"x{i}", ticker="AAA", qty="10", price="1")))
        out.append(o.filled_qty)
    return out


def test_partial_fill_fraction_is_ten_percent_within_two_points():
    pattern = _pattern(7, 2000)
    partial = sum(1 for q in pattern if q < 10)
    assert 160 <= partial <= 240   # 8% a 12% de 2.000
    assert all(q in (D("5"), D("10")) for q in pattern)   # parcial = metade, nunca outro valor


def test_same_seed_repeats_the_pattern_and_another_seed_changes_it():
    assert _pattern(7, 300) == _pattern(7, 300)
    assert _pattern(7, 300) != _pattern(8, 300)


def test_one_share_orders_never_come_partial():
    sim = SimBroker(seed=7, partial_prob=1.0)
    o = run(sim.submit(req(qty="1")))
    assert o.filled_qty == D("1") and o.status == "filled"
    o = run(sim.submit(req(cid="b", qty="10")))
    assert o.filled_qty == D("5") and o.status == "partially_filled"
