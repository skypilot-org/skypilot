"""This module helps generating timelines of an application.

The timeline follows the trace event format defined here:
https://docs.google.com/document/d/1CvAClvFfyA5R-PhYUmn5OOQtYMH4h6I0nSsKchNAySU/preview

Usage:
    SKYPILOT_TIMELINE_FILE_PATH=/tmp/trace.json sky launch ...

    Then open /tmp/trace.json in https://ui.perfetto.dev/ to see a unified
    timeline of client-side and server-side events.

Events are request-scoped on the API server (using contextvars) so that
concurrent requests don't contaminate each other.  On the client side,
events are process-scoped and server-side events are auto-merged into the
output file when the request completes.
"""  # pylint: disable=line-too-long
import atexit
import contextvars
import json
import os
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Union

from sky.utils import common_utils

# ---------------------------------------------------------------------------
# Request-scoped storage (set per-request on the API server)
# ---------------------------------------------------------------------------
_context_events: contextvars.ContextVar[Optional[List[Dict[str, Any]]]] = (
    contextvars.ContextVar('_timeline_events', default=None))
_context_request_id: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar('_timeline_request_id', default=None))
_context_save_path: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar('_timeline_save_path', default=None))

# ---------------------------------------------------------------------------
# Process-global fallback (CLI mode / process-executor workers)
# ---------------------------------------------------------------------------
_process_events: List[Dict[str, Any]] = []
_events_lock = threading.Lock()


def _get_events() -> List[Dict[str, Any]]:
    """Return the event list for the current context."""
    ctx_events = _context_events.get(None)
    if ctx_events is not None:
        return ctx_events
    return _process_events


def _is_tracing_enabled() -> bool:
    if _context_save_path.get(None) is not None:
        return True
    return bool(os.environ.get('SKYPILOT_TIMELINE_FILE_PATH'))


def _get_save_path() -> Optional[str]:
    ctx_path = _context_save_path.get(None)
    if ctx_path is not None:
        return ctx_path
    return os.environ.get('SKYPILOT_TIMELINE_FILE_PATH')


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def init_request_timeline(request_id: str, save_path: str) -> None:
    """Initialise per-request timeline collection on the API server.

    Each request gets its own event list so that concurrent requests in the
    same process do not contaminate each other.
    """
    _context_events.set([])
    _context_request_id.set(request_id)
    _context_save_path.set(save_path)


def set_request_id(request_id: str) -> None:
    """Tag subsequent events with *request_id* (client-side).

    Called by the SDK after receiving the request_id from the server so that
    the client-side trace events can be correlated with the server-side ones.
    """
    _context_request_id.set(request_id)


def add_events(events: List[Dict[str, Any]]) -> None:
    """Add external events (e.g. fetched from the server) to the current
    context's event list so they are included in the next save."""
    with _events_lock:
        _get_events().extend(events)


class Event:
    """Record an event.

    Args:
        name: The name of the event.
        message: The message attached to the event.
    """

    def __init__(self, name: str, message: Optional[str] = None):
        self._skipped = False
        if not _is_tracing_enabled():
            self._skipped = True
            return
        self._name = name
        self._message = message
        request_id = _context_request_id.get(None)
        # See the module doc for the event format.
        self._event: Dict[str, Any] = {
            'name': self._name,
            'cat': 'event',
            'pid': str(os.getpid()),
            'tid': str(threading.current_thread().ident),
            'args': {}
        }
        if request_id is not None:
            self._event['args']['request_id'] = request_id
        if self._message is not None:
            self._event['args']['message'] = self._message

    def begin(self):
        if self._skipped:
            return
        event_begin = self._event.copy()
        event_begin.update({
            'ph': 'B',
            'ts': round(time.time() * 10**6, 3),
        })
        event_begin['args'] = {
            **self._event.get('args', {}),
            'stack': '\n'.join(traceback.format_stack()),
        }
        with _events_lock:
            _get_events().append(event_begin)

    def end(self):
        if self._skipped:
            return
        event_end = self._event.copy()
        event_end.update({
            'ph': 'E',
            'ts': round(time.time() * 10**6, 3),
        })
        event_end['args'] = dict(self._event.get('args', {}))
        with _events_lock:
            _get_events().append(event_end)

    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end()


def event(name_or_fn: Union[str, Callable], message: Optional[str] = None):
    return common_utils.make_decorator(Event, name_or_fn, message=message)


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------
def _write_events_to_file(events: List[Dict[str, Any]], path: str) -> None:
    json_output = {
        'traceEvents': events,
        'displayTimeUnit': 'ms',
        'otherData': {
            'log_dir': os.path.dirname(os.path.abspath(path)),
        }
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(json_output, f)


def save_timeline() -> None:
    """Save and clear timeline events for the current context."""
    save_path = _get_save_path()
    if not save_path:
        return
    with _events_lock:
        events = _get_events()
        events_to_write = list(events)
        events.clear()
    if not events_to_write:
        return
    _write_events_to_file(events_to_write, save_path)


# ---------------------------------------------------------------------------
# atexit – only effective in CLI / single-process mode
# ---------------------------------------------------------------------------
def _atexit_save():
    save_timeline()


if os.environ.get('SKYPILOT_TIMELINE_FILE_PATH'):
    atexit.register(_atexit_save)
