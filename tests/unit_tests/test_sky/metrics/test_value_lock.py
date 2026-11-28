"""Unit tests for sky.metrics.value_lock."""
import os
import subprocess
import sys
import threading
import time
from unittest import mock

from prometheus_client import multiprocess
from prometheus_client import values as prom_values
import prometheus_client as prom
import pytest

from sky.metrics import value_lock

_THREADS = 8


def _shared_lock(value_class):
    """The lock that all values of a multiprocess value class share."""
    inc = value_class.inc
    return inc.__closure__[inc.__code__.co_freevars.index('lock')].cell_contents


def _run_in_threads(target):
    threads = [threading.Thread(target=target) for _ in range(_THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


@pytest.fixture
def multiprocess_values(tmp_path, monkeypatch):
    """Real multiprocess values, with the lock prometheus_client gives them."""
    monkeypatch.setenv('PROMETHEUS_MULTIPROC_DIR', str(tmp_path))
    value_class = prom_values.MultiProcessValue()
    monkeypatch.setattr(prom_values, 'ValueClass', value_class)
    return value_class


def test_spin_lock_excludes_while_waiters_retry():
    """Each thread gives up the GIL between its read and its write, so the
    other threads find the lock held and retry. No update may be lost."""
    lock = value_lock._SpinLock()  # pylint: disable=protected-access
    rounds = 200
    total = [0]

    def add():
        for _ in range(rounds):
            with lock:
                seen = total[0]
                time.sleep(0)
                total[0] = seen + 1

    _run_in_threads(add)
    assert total[0] == _THREADS * rounds


def test_spin_lock_is_released_when_the_body_raises():
    lock = value_lock._SpinLock()  # pylint: disable=protected-access
    with pytest.raises(RuntimeError):
        with lock:
            raise RuntimeError('boom')
    # pylint: disable=protected-access
    assert lock._lock.acquire(blocking=False)


def test_install_replaces_the_lock_and_updates_still_land(
        tmp_path, multiprocess_values):
    assert not isinstance(_shared_lock(multiprocess_values),
                          value_lock._SpinLock)  # pylint: disable=protected-access

    value_lock.install()

    installed = _shared_lock(multiprocess_values)
    # pylint: disable=protected-access
    assert isinstance(installed, value_lock._SpinLock)
    counter = prom.Counter('sky_test_value_lock', 'Test.', registry=None)
    increments = 2000
    _run_in_threads(lambda: [counter.inc() for _ in range(increments)])
    registry = prom.CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=str(tmp_path))
    assert (registry.get_sample_value('sky_test_value_lock_total') == _THREADS *
            increments)

    value_lock.install()
    assert _shared_lock(multiprocess_values) is installed


def test_install_does_nothing_outside_multiprocess_mode(monkeypatch):
    monkeypatch.setattr(prom_values, 'ValueClass', prom_values.MutexValue)
    with mock.patch.object(value_lock.logger, 'warning') as warning:
        value_lock.install()
    warning.assert_not_called()
    counter = prom.Counter('sky_test_value_lock_single', 'Test.', registry=None)
    counter.inc()
    # pylint: disable=protected-access
    assert counter._value.get() == 1


def test_install_warns_when_the_lock_is_not_where_it_expects(monkeypatch):

    class _ValuesWithoutTheSharedLock:
        _multiprocess = True

        def inc(self, amount):
            del amount

    monkeypatch.setattr(prom_values, 'ValueClass', _ValuesWithoutTheSharedLock)
    with mock.patch.object(value_lock.logger, 'warning') as warning:
        value_lock.install()
    warning.assert_called_once()
    assert 'does not keep its lock' in warning.call_args[0][0]


def test_importing_sky_metrics_installs_the_lock_in_multiprocess_mode(tmp_path):
    """A server process has PROMETHEUS_MULTIPROC_DIR set before it starts."""
    env = dict(os.environ, PROMETHEUS_MULTIPROC_DIR=str(tmp_path))
    program = ('import sky.metrics\n'
               'from prometheus_client import values\n'
               'inc = values.ValueClass.inc\n'
               'lock = inc.__closure__['
               'inc.__code__.co_freevars.index("lock")].cell_contents\n'
               'print(type(lock).__name__)\n')
    result = subprocess.run([sys.executable, '-c', program],
                            env=env,
                            capture_output=True,
                            text=True,
                            check=True)
    assert result.stdout.splitlines()[-1] == '_SpinLock'
