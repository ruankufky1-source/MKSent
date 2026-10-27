"""Um laço de eventos só para os testes novos. No Windows, criar um laço novo (asyncio.run) trava de vez
em quando num accept() do socket interno; com 100+ testes isso derrubava 1 rodada em ~10."""
import asyncio

_LOOP = None


def run(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP.run_until_complete(coro)
