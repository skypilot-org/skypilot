"""Regression test for `_consolidated_launch` returncode propagation.

`_consolidated_launch` historically discarded the return code of the
controller subprocess. A non-zero exit (e.g. interpreter mismatch, DB
unreachable, OOM during scheduler_set_waiting) was silently swallowed,
leaving a PENDING+INACTIVE job row in the DB while the API returned
SUCCEEDED to the user. This test pins the new behavior: non-zero
returncode must raise so the request is marked FAILED.
"""

from unittest import mock

import pytest

from sky import exceptions
from sky.jobs.server import core as jobs_core


def _make_mock_backend(returncode: int) -> mock.MagicMock:
    """A CloudVmRayBackend stub that returns the given returncode."""
    backend = mock.MagicMock()
    backend.run_on_head.return_value = returncode
    # sync_file_mounts is a no-op for our purposes.
    backend.sync_file_mounts = mock.MagicMock()
    return backend


def _patch_consolidated_launch_deps(monkeypatch, returncode: int) -> None:
    """Wire up the minimum surface needed for `_consolidated_launch` to run.

    We mock everything `_consolidated_launch` reaches for *before* the
    `run_on_head` call so the function gets to the returncode check with
    a controlled value.
    """
    fake_handle = mock.MagicMock()
    monkeypatch.setattr(
        'sky.backends.backend_utils.is_controller_accessible',
        lambda **kwargs: fake_handle,
    )
    backend = _make_mock_backend(returncode)
    monkeypatch.setattr(
        'sky.backends.backend_utils.get_backend_from_handle',
        lambda _h: backend,
    )
    # _consolidated_launch asserts the backend is a CloudVmRayBackend; the
    # asserted type comes from `sky.backends`. Make isinstance() happy.
    import sky.backends as sky_backends
    monkeypatch.setattr(sky_backends, 'CloudVmRayBackend', mock.MagicMock)


def _make_controller_task() -> mock.MagicMock:
    task = mock.MagicMock()
    task.run = 'echo "test run script"'
    task.envs = {}
    task.file_mounts = None
    task.storage_mounts = None
    return task


def test_nonzero_returncode_raises_command_error(monkeypatch):
    """A non-zero exit from the controller subprocess must raise so the
    user's `jobs.launch` request is marked FAILED."""
    _patch_consolidated_launch_deps(monkeypatch, returncode=42)

    controller = mock.MagicMock()
    task = _make_controller_task()

    with pytest.raises(exceptions.CommandError) as excinfo:
        jobs_core._consolidated_launch(controller, task, job_ids=[7])
    # Surface the returncode and a hint to cancel-and-retry.
    assert excinfo.value.returncode == 42
    assert 'sky jobs cancel 7' in excinfo.value.error_msg
    assert 'returncode=42' in excinfo.value.error_msg


def test_zero_returncode_succeeds(monkeypatch):
    """Happy path: returncode 0 should return job_ids without raising."""
    _patch_consolidated_launch_deps(monkeypatch, returncode=0)

    controller = mock.MagicMock()
    task = _make_controller_task()

    job_ids, handle = jobs_core._consolidated_launch(controller,
                                                     task,
                                                     job_ids=[7])
    assert job_ids == [7]
    assert handle is not None


def test_multiple_job_ids_in_error_message(monkeypatch):
    """The cancel hint must list all job ids in the same compact form
    that `_job_ids_to_str` emits."""
    _patch_consolidated_launch_deps(monkeypatch, returncode=1)

    controller = mock.MagicMock()
    task = _make_controller_task()

    with pytest.raises(exceptions.CommandError) as excinfo:
        jobs_core._consolidated_launch(controller, task, job_ids=[10, 11, 12])
    # _job_ids_to_str collapses contiguous ranges.
    assert 'sky jobs cancel 10-12' in excinfo.value.error_msg
