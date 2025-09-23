"""Utility functions for threads."""

import threading


class SafeThread(threading.Thread):
    """A thread that can catch exceptions."""

    def __init__(self, target, args=(), kwargs=None):
        super().__init__()
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.exc = None

    def run(self):
        try:
            self._target(*self._args, **self._kwargs)
        except Exception as e:  # pylint: disable=broad-except
            self.exc = e

    @property
    def exitcode(self) -> int:
        return 1 if self.exc is not None else 0
