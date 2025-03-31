"""Atomic structures and utilties."""

import threading


class AtomicInt:
    """A thread-safe atomic integer implementation."""

    def __init__(self, initial_value: int = 0):
        self._value = initial_value
        self._lock = threading.Lock()

    def get(self) -> int:
        """Get the current value atomically.

        Returns:
            The current integer value.
        """
        with self._lock:
            return self._value

    def __iadd__(self, delta: int) -> 'AtomicInt':
        """Atomically increment by delta and return self.

        Args:
            delta: Amount to increment by

        Returns:
            Self reference to support the += operator
        """
        with self._lock:
            self._value += delta
            return self

    def __isub__(self, delta: int) -> 'AtomicInt':
        """Atomically decrement by delta and return self.

        Args:
            delta: Amount to decrement by

        Returns:
            Self reference to support the -= operator
        """
        with self._lock:
            self._value -= delta
            return self

    def decrement(self):
        """Atomically decrement by 1"""
        self -= 1

    def __str__(self) -> str:
        return str(self.get())

    def __repr__(self) -> str:
        return f'AtomicInt({self.get()})'
