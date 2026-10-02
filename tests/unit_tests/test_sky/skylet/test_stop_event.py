"""Unit tests for StopEvent's worker-then-head teardown in the skylet."""
import os
from unittest import mock

import pytest

from sky import provision as provision_lib
from sky.skylet import autostop_lib
from sky.skylet import events

_CLUSTER_CONFIG = {
    'cluster_name': 'mycluster-aaaa',
    'max_workers': 1,
    'provider': {
        'region': 'eu-north1'
    },
}


def _run_teardown(down: bool, fn_name: str, side_effect):
    """Runs the multi-node teardown with the provision call mocked.

    `side_effect` is applied to the worker-only call; the head call succeeds.
    Returns the mocked provision function.
    """
    # Skip __init__: it touches the autostop state on disk.
    event = events.StopEvent.__new__(events.StopEvent)
    config = mock.MagicMock(down=down)
    cloud = mock.MagicMock()
    cloud.uses_ray.return_value = False

    def fake_op(*_args, worker_only=False, **_kwargs):
        if worker_only:
            side_effect()

    # The method rewrites AWS credential env vars; keep them out of the
    # test process.
    with mock.patch.dict(os.environ), \
         mock.patch.object(autostop_lib, 'set_autostopping_started'), \
         mock.patch.object(event, '_execute_hook_if_present'), \
         mock.patch.object(provision_lib, fn_name,
                           side_effect=fake_op) as mock_op:
        # pylint: disable-next=protected-access
        event._stop_cluster_with_new_provisioner(config, _CLUSTER_CONFIG,
                                                 'nebius', cloud)
    return mock_op


def _raise_timeout():
    raise RuntimeError('Timed out waiting for the worker instances')


def test_autodown_continues_to_head_after_worker_failure():
    """A worker-only failure must not strand the head during autodown."""
    mock_op = _run_teardown(down=True,
                            fn_name='terminate_instances',
                            side_effect=_raise_timeout)
    assert mock_op.call_count == 2
    assert mock_op.call_args_list[0].kwargs['worker_only'] is True
    # The second call is the full termination (no worker_only).
    assert 'worker_only' not in mock_op.call_args_list[1].kwargs


def test_autostop_still_raises_on_worker_failure():
    """Autostop (stop, not down) keeps propagating worker failures."""
    with pytest.raises(RuntimeError, match='Timed out'):
        _run_teardown(down=False,
                      fn_name='stop_instances',
                      side_effect=_raise_timeout)


def test_autodown_terminates_workers_then_head():
    mock_op = _run_teardown(down=True,
                            fn_name='terminate_instances',
                            side_effect=lambda: None)
    assert [c.kwargs.get('worker_only') for c in mock_op.call_args_list
           ] == [True, None]
