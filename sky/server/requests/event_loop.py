"""Executor event loop to process tasks in coroutines."""
import asyncio
import concurrent.futures
import threading
from typing import Coroutine, Optional

# Dedicated event loop for requests, isolated with the event loop managed
# by uvicorn. This is responsible for light-weight async tasks or sub-tasks,
# refer to `executor.py` for more details about cooperation between the event
# loop and executor process pool.
_EVENT_LOOP: Optional[asyncio.AbstractEventLoop] = None
_LOCK = threading.Lock()


def run(coro: Coroutine) -> concurrent.futures.Future:
    """Run a coroutine asynchronously in the request event loop."""
    return asyncio.run_coroutine_threadsafe(coro, get_event_loop())


def get_event_loop() -> asyncio.AbstractEventLoop:
    """Open and get the event loop."""
    global _EVENT_LOOP
    if _EVENT_LOOP is not None and not _EVENT_LOOP.is_closed():
        return _EVENT_LOOP
    with _LOCK:
        if _EVENT_LOOP is None or _EVENT_LOOP.is_closed():
            _EVENT_LOOP = asyncio.new_event_loop()
            loop_thread = threading.Thread(target=_EVENT_LOOP.run_forever,
                                           daemon=True)
            loop_thread.start()
    return _EVENT_LOOP
