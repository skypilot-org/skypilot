"""Tests for the time_me decorator.

Durations are read from the process-global prometheus registry, so each test
uses a distinctly named function to keep its series to itself.
"""
import asyncio
import contextlib
import time

import prometheus_client as prom
import pytest

from sky.metrics import utils as metrics_utils

_SLEEP = 0.05


@pytest.fixture(autouse=True)
def _enable_metrics(monkeypatch):
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)


def _duration(func_name):
    value = prom.REGISTRY.get_sample_value(
        'sky_apiserver_code_duration_seconds_sum', {
            'name': f'{__name__}/{func_name}',
            'group': 'function',
        })
    return 0.0 if value is None else value


def test_time_me_times_sync_execution():

    @metrics_utils.time_me
    def sync_fn():
        time.sleep(_SLEEP)
        return 'done'

    assert sync_fn() == 'done'
    assert _duration('sync_fn') >= _SLEEP


def test_time_me_times_coroutine_execution():

    @metrics_utils.time_me
    async def coroutine_fn():
        await asyncio.sleep(_SLEEP)
        return 'done'

    assert asyncio.run(coroutine_fn()) == 'done'
    assert _duration('coroutine_fn') >= _SLEEP


def test_time_me_times_generator_execution():

    @contextlib.contextmanager
    @metrics_utils.time_me
    def generator_fn():
        yield 'value'

    with generator_fn() as value:
        assert value == 'value'
        time.sleep(_SLEEP)
    assert _duration('generator_fn') >= _SLEEP


def test_time_me_records_on_exception():

    @metrics_utils.time_me
    async def raising_fn():
        await asyncio.sleep(_SLEEP)
        raise ValueError('boom')

    with pytest.raises(ValueError):
        asyncio.run(raising_fn())
    assert _duration('raising_fn') >= _SLEEP
