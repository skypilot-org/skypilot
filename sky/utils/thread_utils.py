"""Utility functions for threads."""

import threading
from typing import Any, Dict, Generic, Optional, overload, TypeVar

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


# pylint: disable=invalid-name
KeyType = TypeVar('KeyType')
ValueType = TypeVar('ValueType')


# Google style guide: Do not rely on the atomicity of built-in types.
# Our launch and down process pool will be used by multiple threads,
# therefore we need to use a thread-safe dict.
# see https://google.github.io/styleguide/pyguide.html#218-threading
class ThreadSafeDict(Generic[KeyType, ValueType]):
    """A thread-safe dict."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._dict: Dict[KeyType, ValueType] = dict(*args, **kwargs)
        self._lock = threading.Lock()

    def __getitem__(self, key: KeyType) -> ValueType:
        with self._lock:
            return self._dict.__getitem__(key)

    def __setitem__(self, key: KeyType, value: ValueType) -> None:
        with self._lock:
            return self._dict.__setitem__(key, value)

    def __delitem__(self, key: KeyType) -> None:
        with self._lock:
            return self._dict.__delitem__(key)

    def __len__(self) -> int:
        with self._lock:
            return self._dict.__len__()

    def __contains__(self, key: KeyType) -> bool:
        with self._lock:
            return self._dict.__contains__(key)

    def items(self):
        with self._lock:
            return self._dict.items()

    def values(self):
        with self._lock:
            return self._dict.values()

    @overload
    def get(self, key: KeyType, default: ValueType) -> ValueType:
        ...

    @overload
    def get(self,
            key: KeyType,
            default: Optional[ValueType] = None) -> Optional[ValueType]:
        ...

    def get(self,
            key: KeyType,
            default: Optional[ValueType] = None) -> Optional[ValueType]:
        with self._lock:
            return self._dict.get(key, default)

    def pop(self, key: KeyType) -> Optional[ValueType]:
        with self._lock:
            return self._dict.pop(key, None)
