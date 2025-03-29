"""Atomic structures and utilties."""

import threading


class Int:
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

    def set(self, value: int) -> None:
        """Set the value atomically.

        Args:
            value: The new integer value to set.
        """
        with self._lock:
            self._value = value

    def increment(self, delta: int = 1) -> int:
        """Atomically increment by delta and return new value.

        Args:
            delta: Amount to increment by (default: 1)

        Returns:
            The new value after incrementing.
        """
        with self._lock:
            self._value += delta
            return self._value

    def decrement(self, delta: int = 1) -> int:
        """Atomically decrement by delta and return new value.

        Args:
            delta: Amount to decrement by (default: 1)

        Returns:
            The new value after decrementing.
        """
        with self._lock:
            self._value -= delta
            return self._value

    def compare_and_set(self, expect: int, update: int) -> bool:
        """Atomically set to update if current value equals expect.

        Args:
            expect: The expected current value
            update: The new value to set if current equals expect

        Returns:
            True if the value was updated, False otherwise.
        """
        with self._lock:
            if self._value == expect:
                self._value = update
                return True
            return False

    def __str__(self) -> str:
        return str(self.get())

    def __repr__(self) -> str:
        return f'AtomicInt({self.get()})'
