"""Utility functions for threads."""

import threading
from typing import Optional

from sky.utils import common_utils


class SafeThread(threading.Thread):
    """A thread that can catch exceptions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exc = None

    def run(self):
        try:
            super().run()
        except BaseException as e:  # pylint: disable=broad-except
            self._exc = e

    @property
    def format_exc(self) -> Optional[str]:
        if self._exc is None:
            return None
        return common_utils.format_exception(self._exc)
