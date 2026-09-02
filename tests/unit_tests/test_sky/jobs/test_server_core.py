"""Tests for sky.jobs.server.core."""
from unittest import mock

import pytest

from sky import backends
from sky.jobs.server import core as jobs_core


def _forwarded_tail(tail):
    """Call ``jobs_core.tail_logs`` with ``tail`` (mocking out the controller
    restart / backend / runner) and return the ``tail`` value forwarded to
    ``tail_managed_job_logs``."""
    fake_backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    fake_runner = mock.MagicMock()
    fake_runner.tail_managed_job_logs.return_value = 0
    with mock.patch.object(jobs_core, '_maybe_restart_controller',
                           return_value=mock.MagicMock()), \
         mock.patch.object(jobs_core.backend_utils,
                           'get_backend_from_handle',
                           return_value=fake_backend), \
         mock.patch.object(jobs_core.managed_job_runner,
                           'current',
                           return_value=fake_runner):
        jobs_core.tail_logs(name=None,
                            job_id=1,
                            follow=False,
                            controller=False,
                            refresh=False,
                            tail=tail)
    fake_runner.tail_managed_job_logs.assert_called_once()
    return fake_runner.tail_managed_job_logs.call_args.kwargs['tail']


@pytest.mark.parametrize(
    ('given', 'expected'),
    [
        (0, None),  # dashboard download button's "all lines" sentinel
        (-1, None),  # `sky jobs logs --tail -1`
        (None, None),  # no tail -> all
        (200, 200),  # positive tail forwarded unchanged
        (5000, 5000),
    ])
def test_tail_logs_normalizes_non_positive_tail(given, expected):
    """A non-positive tail (0 / -1) means "all lines" and must be normalized
    to None before reaching the backward-seek tail reader (which asserts
    tail > 0). Otherwise the dashboard download (tail=0) raises
    AssertionError and produces an empty log."""
    assert _forwarded_tail(given) == expected
