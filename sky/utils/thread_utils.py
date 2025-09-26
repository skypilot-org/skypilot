"""Utility functions for threads."""

import threading


class SafeThread(threading.Thread):
    """A thread that can catch exceptions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exc = None

    def run(self):
        try:
            super().run()
        except Exception as e:  # pylint: disable=broad-except
            self._exc = e

    @property
    def exitcode(self) -> int:
        return 1 if self._exc is not None else 0
