"""Tests for waiting on a Nebius cluster's pending instances."""

from typing import List
from unittest import mock

import pytest

from sky.provision.nebius import instance


def _wait(monkeypatch, pending_per_poll: List[List[str]],
          max_retries: int) -> int:
    """Runs _wait_until_no_pending over scripted pending sets; returns polls."""
    polls = iter(pending_per_poll)
    calls = []

    def fake_filter(*args, **kwargs):
        del args, kwargs
        ids = next(polls)
        calls.append(ids)
        return {i: {'status': 'STARTING'} for i in ids}

    monkeypatch.setattr(instance, '_filter_instances', fake_filter)
    monkeypatch.setattr(instance, 'MAX_RETRIES_TO_LAUNCH', max_retries)
    monkeypatch.setattr(instance, 'time', mock.Mock())
    instance._wait_until_no_pending('eu-north1', 'c', project_id='p')
    return len(calls)


def test_steady_progress_outlasts_the_budget(monkeypatch):
    # Six nodes, one leaves STARTING every two polls: 13 polls in all, far
    # past a 3-poll budget, but never 3 polls in a row without progress.
    nodes = [f'i{n}' for n in range(6)]
    seq = []
    for remaining in range(6, 0, -1):
        seq += [nodes[:remaining]] * 2
    seq.append([])
    assert _wait(monkeypatch, seq, max_retries=3) == len(seq)


def test_times_out_without_progress(monkeypatch):
    with pytest.raises(TimeoutError, match='No instance left STARTING'):
        _wait(monkeypatch, [['a', 'b']] * 10, max_retries=3)


def test_a_newly_pending_instance_is_not_progress(monkeypatch):
    seq = [['a'], ['a', 'b'], ['a', 'b'], ['a', 'b'], ['a', 'b']]
    with pytest.raises(TimeoutError):
        _wait(monkeypatch, seq, max_retries=3)
