"""Bridge cancellable async I/O into the existing synchronous streaming UI.

HTTP read timeouts reset on every chunk. wait_for instead bounds the entire
request, including a provider that keeps sending chunks without ever finishing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import suppress
from queue import Queue
from threading import Event, Thread
from typing import Any

from .runtime import BudgetExceeded


def iterate_request(factory: Callable, timeout_s: float) -> Iterator[Any]:
    events: Queue = Queue()
    ready = Event()
    state: dict = {}

    async def execute():
        state["loop"] = asyncio.get_running_loop()
        state["task"] = asyncio.current_task()
        ready.set()
        try:
            await asyncio.wait_for(factory(events.put), timeout=timeout_s)
        except TimeoutError:
            events.put(("error", BudgetExceeded("整轮处理时间已达上限")))
        except Exception as exc:  # noqa: BLE001 - forward to the synchronous caller
            events.put(("error", exc))
        finally:
            events.put(("done", None))

    def worker():
        with suppress(asyncio.CancelledError):
            asyncio.run(execute())

    thread = Thread(target=worker, name="acs-provider-request", daemon=True)
    thread.start()
    ready.wait()
    try:
        while True:
            kind, value = events.get()
            if kind == "done":
                break
            if kind == "error":
                raise value
            yield value
    finally:
        loop, task = state["loop"], state["task"]
        if not loop.is_closed():
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(task.cancel)
        thread.join(timeout=2)
