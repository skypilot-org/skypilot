"""This module helps generating timelines of an application."""
from typing import Optional, Union, Callable

import atexit
import json
import os
import threading
import time
import inspect

import filelock

_events = []


class Event:
    """Record an event.

    Args:
        name: The name of the event.
        message: The message attached to the event.
    """

    def __init__(self, name: str, message: str = None):
        self._name = name
        self._message = message
        self._event = {
            'name': self._name,
            'cat': 'event',
            'pid': str(os.getpid()),
            'tid': str(threading.current_thread().ident),
            'args': {
                'message': self._message
            }
        }
        if self._message is not None:
            self._event['args'] = {'message': self._message}

    def __enter__(self):
        event_begin = self._event.copy()
        event_begin.update({
            'ph': 'B',
            'ts': f'{time.time() * 10 ** 6: .3f}',
        })
        if self._message is not None:
            event_begin['args'] = {'message': self._message}
        global _events
        _events.append(event_begin)

    def __exit__(self, exc_type, exc_val, exc_tb):
        event_end = self._event.copy()
        event_end.update({
            'ph': 'E',
            'ts': f'{time.time() * 10 ** 6: .3f}',
        })
        if self._message is not None:
            event_end['args'] = {'message': self._message}
        global _events
        _events.append(event_end)


class LockEvent:
    """Serve both as a file lock and event for the lock."""

    def __init__(self, lockfile: Union[str, os.PathLike]):
        self._lockfile = lockfile
        # TODO(mraheja): remove pylint disabling when filelock version updated
        # pylint: disable=abstract-class-instantiated
        self._lock = filelock.FileLock(self._lockfile)
        self._event = Event(f'[Lock.keep]:{self._lockfile}')

    def __enter__(self):
        with Event(f'[Lock.acquire]:{self._lockfile}'):
            lock = self._lock.__enter__()
            self._event.__enter__()
            return lock

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._lock.__exit__(exc_type, exc_val, exc_tb)
        self._event.__exit__(exc_type, exc_val, exc_tb)


def event(name: Union[str, Callable], message: Optional[str] = None):
    """A decorator for logging events when applied to functions.

    Args:
        name: The name of the event.
        message: The message attached to the event.
    """
    if isinstance(name, str):

        def _wrapper(f):

            def _record(*args, **kwargs):
                nonlocal name
                if name is None:
                    name = getattr(f, '__qualname__', f.__name__)
                with Event(name=name, message=message):
                    return f(*args, **kwargs)

            return _record

        return _wrapper
    else:
        if not inspect.isfunction(name):
            raise ValueError(
                'Should directly apply the decorator to a function.')

        def _record(*args, **kwargs):
            nonlocal name
            f = name
            func_name = getattr(f, '__qualname__', f.__name__)
            module_name = getattr(f, '__module__', '')
            if module_name:
                full_name = f'{module_name}.{func_name}'
            else:
                full_name = func_name
            with Event(name=full_name, message=message):
                return f(*args, **kwargs)

        return _record


def _save_timeline(file_path: str):
    json_output = {
        'traceEvents': _events,
        'displayTimeUnit': 'ms',
        'otherData': {
            'log_dir': os.path.dirname(file_path),
        }
    }
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w') as f:
        json.dump(json_output, f)


if os.environ.get('SKY_TIMELINE_FILE_PATH'):
    atexit.register(_save_timeline, os.environ['SKY_TIMELINE_FILE_PATH'])
