"""This module helps generating timelines of an application.

The timeline follows the trace event format defined here:
https://docs.google.com/document/d/1CvAClvFfyA5R-PhYUmn5OOQtYMH4h6I0nSsKchNAySU/preview
"""  # pylint: disable=line-too-long
import atexit
import json
import os
import threading
import time
import traceback
from typing import Callable, Optional, Union

from sky.utils import common_utils

_events = []


def _get_events_file_path():
    return os.environ.get('SKYPILOT_TIMELINE_FILE_PATH')


class Event:
    """Record an event.

    Args:
        name: The name of the event.
        message: The message attached to the event.
    """

    def __init__(self, name: str, message: Optional[str] = None):
        self._skipped = False
        if not _get_events_file_path():
            self._skipped = True
            return
        self._name = name
        self._message = message
        # See the module doc for the event format.
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

    def begin(self):
        if self._skipped:
            return
        event_begin = self._event.copy()
        event_begin.update({
            'ph': 'B',
            'ts': f'{time.time() * 10 ** 6: .3f}',
        })
        event_begin['args'] = {'stack': '\n'.join(traceback.format_stack())}
        if self._message is not None:
            event_begin['args']['message'] = self._message
        _events.append(event_begin)

    def end(self):
        if self._skipped:
            return
        event_end = self._event.copy()
        event_end.update({
            'ph': 'E',
            'ts': f'{time.time() * 10 ** 6: .3f}',
        })
        if self._message is not None:
            event_end['args'] = {'message': self._message}
        _events.append(event_end)

    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end()


def event(name_or_fn: Union[str, Callable], message: Optional[str] = None):
    return common_utils.make_decorator(Event, name_or_fn, message=message)


def save_timeline():
    events_file_path = _get_events_file_path()
    if not events_file_path:
        return
    global _events
    events_to_write = _events
    _events = []
    json_output = {
        'traceEvents': events_to_write,
        'displayTimeUnit': 'ms',
        'otherData': {
            'log_dir': os.path.dirname(os.path.abspath(events_file_path)),
        }
    }
    os.makedirs(os.path.dirname(os.path.abspath(events_file_path)),
                exist_ok=True)
    with open(events_file_path, 'w', encoding='utf-8') as f:
        json.dump(json_output, f)
    del events_to_write


if _get_events_file_path():
    atexit.register(save_timeline)
