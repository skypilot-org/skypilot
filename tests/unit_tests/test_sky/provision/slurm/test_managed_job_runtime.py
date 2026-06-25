"""Unit tests for sky.provision.slurm.managed_job_runtime.

Covers:
- ``_slurm_state_to_job_status`` mapping per Slurm state class.
- ``_resolve_slurm_target`` happy + every refusal path.
- ``SlurmManagedJobRuntime.get_job_status`` returncode shortcut +
  state-driven path.
"""
# pylint: disable=protected-access,missing-class-docstring
import types
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.slurm import managed_job_runtime as mjr
from sky.skylet import job_lib

# --------------------- _slurm_state_to_job_status --------------------- #


class TestSlurmStateToJobStatus:
    """Map every documented Slurm state to the expected JobStatus."""

    @pytest.mark.parametrize('state', [
        'PENDING',
        'CONFIGURING',
        'RESV_DEL_HOLD',
        'REQUEUED',
        'REQUEUE_HOLD',
        'REQUEUE_FED',
        'RESIZING',
    ])
    def test_pending_states(self, state):
        assert mjr._slurm_state_to_job_status(
            state) == job_lib.JobStatus.PENDING

    @pytest.mark.parametrize('state', [
        'RUNNING',
        'COMPLETING',
        'SIGNALING',
        'STAGE_OUT',
        'SUSPENDED',
    ])
    def test_running_states(self, state):
        assert mjr._slurm_state_to_job_status(
            state) == job_lib.JobStatus.RUNNING

    def test_completed_is_succeeded(self):
        assert mjr._slurm_state_to_job_status(
            'COMPLETED') == job_lib.JobStatus.SUCCEEDED

    @pytest.mark.parametrize('state', [
        'FAILED',
        'NODE_FAIL',
        'BOOT_FAIL',
        'TIMEOUT',
        'OUT_OF_MEMORY',
        'DEADLINE',
        'SPECIAL_EXIT',
        'PREEMPTED',
    ])
    def test_failed_states(self, state):
        assert mjr._slurm_state_to_job_status(state) == job_lib.JobStatus.FAILED

    @pytest.mark.parametrize('state', ['CANCELLED', 'REVOKED'])
    def test_cancelled_states(self, state):
        assert mjr._slurm_state_to_job_status(
            state) == job_lib.JobStatus.CANCELLED

    def test_cancelled_with_uid_suffix_normalized_by_sacct(self):
        """The runtime relies on ``_sacct_get_state`` to normalize
        ``CANCELLED by 12345`` to plain ``CANCELLED`` *before* feeding
        the result into ``_slurm_state_to_job_status``. Verify the
        contract: feeding plain ``CANCELLED`` produces ``CANCELLED``,
        and the normalization in ``_sacct_get_state`` strips the suffix.
        """
        # Plain CANCELLED maps cleanly.
        assert mjr._slurm_state_to_job_status(
            'CANCELLED') == job_lib.JobStatus.CANCELLED

        # _sacct_get_state should strip the suffix:
        client = mock.MagicMock()
        client._run_slurm_cmd = mock.MagicMock(
            return_value=(0, 'CANCELLED by 12345\n', ''))
        assert mjr._sacct_get_state(client, '999') == 'CANCELLED'

    def test_unknown_state_returns_none(self):
        # Don't silently coerce; let the caller default.
        assert mjr._slurm_state_to_job_status('SOME_FUTURE_STATE') is None

    def test_none_input_returns_none(self):
        assert mjr._slurm_state_to_job_status(None) is None


# ----------------------- _resolve_slurm_target ----------------------- #


def _make_handle(*,
                 has_ray=False,
                 cluster_yaml='/fake.yml',
                 cached_cluster_info=None,
                 metadata_present=True,
                 region=None):
    """Build a handle with the attributes ``_resolve_slurm_target`` reads."""
    handle = types.SimpleNamespace()
    if metadata_present:
        handle.provision_runtime_metadata = types.SimpleNamespace(
            has_ray=has_ray)
    handle.cluster_yaml = cluster_yaml
    handle.cached_cluster_info = cached_cluster_info
    handle.launched_resources = types.SimpleNamespace(region=region)
    return handle


def _make_cached_info(head_id='head-0', tags=None):
    """Build a ``cached_cluster_info`` SimpleNamespace shaped like
    ProvisionRecord output."""
    if tags is None:
        tags = {'job_id': '12345'}
    head_info = types.SimpleNamespace(tags=tags)
    return types.SimpleNamespace(head_instance_id=head_id,
                                 instances={head_id: [head_info]})


_V1_PROVIDER_CONFIG = {
    'skypilot_runtime': 'managed_job_v1',
    'ssh': {
        'hostname': 'login.example.com',
        'port': 22,
        'user': 'slurm',
        'private_key': '/tmp/key',
    },
    'partition': 'gpu',
}


class TestResolveSlurmTarget:

    @pytest.fixture
    def patched_yaml(self, monkeypatch):
        """Return a helper that installs a fake cluster_yaml loader."""

        def install(yaml_dict, raise_exc=None):

            def fake_loader(path):
                del path  # Unused.
                if raise_exc is not None:
                    raise raise_exc
                return yaml_dict

            monkeypatch.setattr(mjr.global_user_state, 'get_cluster_yaml_dict',
                                fake_loader)

        return install

    def test_none_handle(self, patched_yaml):
        patched_yaml({'provider': _V1_PROVIDER_CONFIG})
        assert mjr._resolve_slurm_target(None) is None

    def test_has_ray_true_returns_none(self, patched_yaml):
        patched_yaml({'provider': _V1_PROVIDER_CONFIG})
        handle = _make_handle(has_ray=True,
                              cached_cluster_info=_make_cached_info())
        assert mjr._resolve_slurm_target(handle) is None

    def test_metadata_missing_returns_none(self, patched_yaml):
        patched_yaml({'provider': _V1_PROVIDER_CONFIG})
        handle = _make_handle(metadata_present=False,
                              cached_cluster_info=_make_cached_info())
        assert mjr._resolve_slurm_target(handle) is None

    def test_cluster_yaml_unreadable_returns_none(self, patched_yaml):
        patched_yaml(None, raise_exc=ValueError('missing'))
        handle = _make_handle(cached_cluster_info=_make_cached_info())
        assert mjr._resolve_slurm_target(handle) is None

    def test_cluster_yaml_typeerror_returns_none(self, patched_yaml):
        patched_yaml(None, raise_exc=TypeError('bad path'))
        handle = _make_handle(cached_cluster_info=_make_cached_info())
        assert mjr._resolve_slurm_target(handle) is None

    def test_provider_without_v1_marker_returns_none(self, patched_yaml):
        patched_yaml({'provider': {'partition': 'gpu'}})
        handle = _make_handle(cached_cluster_info=_make_cached_info())
        assert mjr._resolve_slurm_target(handle) is None

    def test_cached_cluster_info_none_returns_none(self, patched_yaml):
        patched_yaml({'provider': _V1_PROVIDER_CONFIG})
        handle = _make_handle(cached_cluster_info=None)
        assert mjr._resolve_slurm_target(handle) is None

    def test_missing_head_instance_returns_none(self, patched_yaml):
        patched_yaml({'provider': _V1_PROVIDER_CONFIG})
        cached = types.SimpleNamespace(head_instance_id='missing-head',
                                       instances={})
        handle = _make_handle(cached_cluster_info=cached)
        assert mjr._resolve_slurm_target(handle) is None

    def test_missing_job_id_tag_returns_none(self, patched_yaml):
        """Tag is the primary identity — no regex fallback (PLAN.md gap #8)."""
        patched_yaml({'provider': _V1_PROVIDER_CONFIG})
        cached = _make_cached_info(tags={'other_tag': 'value'})
        handle = _make_handle(cached_cluster_info=cached)
        assert mjr._resolve_slurm_target(handle) is None

    def test_empty_job_id_tag_returns_none(self, patched_yaml):
        patched_yaml({'provider': _V1_PROVIDER_CONFIG})
        cached = _make_cached_info(tags={'job_id': ''})
        handle = _make_handle(cached_cluster_info=cached)
        assert mjr._resolve_slurm_target(handle) is None

    def test_happy_path(self, patched_yaml):
        patched_yaml({'provider': _V1_PROVIDER_CONFIG})
        cached = _make_cached_info(tags={'job_id': '12345'})
        handle = _make_handle(cached_cluster_info=cached, region='partition-x')

        target = mjr._resolve_slurm_target(handle)

        assert target is not None
        assert target.job_id == '12345'
        assert target.partition == 'gpu'
        assert target.ssh_config == _V1_PROVIDER_CONFIG['ssh']
        assert target.region == 'partition-x'


# ------------------- SlurmManagedJobRuntime.get_job_status ------------ #


class TestGetJobStatusReturncodeShortcut:
    """When the caller supplies a returncode, the runtime trusts it and
    skips the Slurm query entirely."""

    @pytest.fixture
    def runtime_and_handle(self, monkeypatch):
        """A runtime + handle pair where ``_resolve_slurm_target`` returns
        a fake target — but ``get_job_state`` would crash if invoked."""

        def fake_resolve(handle):
            del handle  # Unused; resolver is a stub.
            return mjr._Target(job_id='42',
                               ssh_config={
                                   'hostname': 'h',
                                   'user': 'u',
                                   'private_key': '/k',
                               },
                               partition='cpu',
                               region=None,
                               log_path=None)

        monkeypatch.setattr(mjr, '_resolve_slurm_target', fake_resolve)
        # Trip-wire: if the runtime falls through to a Slurm query, fail.
        monkeypatch.setattr(
            mjr, '_slurm_client_from_target',
            mock.MagicMock(side_effect=AssertionError(
                'returncode shortcut must skip Slurm query')))

        return mjr.SlurmManagedJobRuntime(), object()

    def test_succeeded_returncode(self, runtime_and_handle):
        runtime, handle = runtime_and_handle
        result = runtime.get_job_status(
            handle,
            'cluster',
            returncode=exceptions.JobExitCode.SUCCEEDED.value)
        assert result == (job_lib.JobStatus.SUCCEEDED, None)

    def test_cancelled_returncode(self, runtime_and_handle):
        runtime, handle = runtime_and_handle
        result = runtime.get_job_status(
            handle,
            'cluster',
            returncode=exceptions.JobExitCode.CANCELLED.value)
        assert result == (job_lib.JobStatus.CANCELLED, None)

    def test_other_returncode_is_failed(self, runtime_and_handle):
        runtime, handle = runtime_and_handle
        result = runtime.get_job_status(
            handle, 'cluster', returncode=exceptions.JobExitCode.FAILED.value)
        assert result == (job_lib.JobStatus.FAILED, None)

    def test_arbitrary_nonzero_returncode_is_failed(self, runtime_and_handle):
        """Anything that isn't SUCCEEDED or CANCELLED collapses to FAILED."""
        runtime, handle = runtime_and_handle
        result = runtime.get_job_status(handle, 'cluster', returncode=137)
        assert result == (job_lib.JobStatus.FAILED, None)


class TestGetJobStatusStateDriven:
    """returncode is None → the runtime queries Slurm."""

    @pytest.fixture
    def runtime(self):
        return mjr.SlurmManagedJobRuntime()

    @pytest.fixture
    def patched_target(self, monkeypatch):
        """Install a fake target + ``_slurm_client_from_target`` that
        returns a MagicMock the test then configures."""
        fake_client = mock.MagicMock(name='SlurmClient')

        def fake_resolve(handle):
            del handle  # Unused; resolver is a stub.
            return mjr._Target(job_id='42',
                               ssh_config={
                                   'hostname': 'h',
                                   'user': 'u',
                                   'private_key': '/k',
                               },
                               partition='cpu',
                               region=None,
                               log_path=None)

        monkeypatch.setattr(mjr, '_resolve_slurm_target', fake_resolve)
        monkeypatch.setattr(mjr, '_slurm_client_from_target',
                            lambda target: fake_client)
        return fake_client

    def test_unresolved_target_returns_none(self, runtime, monkeypatch):
        monkeypatch.setattr(mjr, '_resolve_slurm_target', lambda handle: None)
        assert runtime.get_job_status(object(), 'cluster',
                                      returncode=None) is None

    def test_squeue_running_state(self, runtime, patched_target):
        patched_target.get_job_state = mock.MagicMock(return_value='RUNNING')
        result = runtime.get_job_status(object(), 'cluster', returncode=None)
        assert result == (job_lib.JobStatus.RUNNING, None)

    def test_squeue_completed_state(self, runtime, patched_target):
        patched_target.get_job_state = mock.MagicMock(return_value='COMPLETED')
        result = runtime.get_job_status(object(), 'cluster', returncode=None)
        assert result == (job_lib.JobStatus.SUCCEEDED, None)

    def test_squeue_none_falls_back_to_sacct(self, runtime, patched_target):
        """When ``get_job_state`` returns None (aged out of squeue), the
        runtime falls back to ``_sacct_get_state``."""
        patched_target.get_job_state = mock.MagicMock(return_value=None)
        # _sacct_get_state runs _run_slurm_cmd directly.
        patched_target._run_slurm_cmd = mock.MagicMock(return_value=(0,
                                                                     'FAILED\n',
                                                                     ''))
        result = runtime.get_job_status(object(), 'cluster', returncode=None)
        assert result == (job_lib.JobStatus.FAILED, None)

    def test_sacct_cancelled_with_uid_suffix(self, runtime, patched_target):
        """sacct emits ``CANCELLED by <uid>``; normalize to CANCELLED."""
        patched_target.get_job_state = mock.MagicMock(return_value=None)
        patched_target._run_slurm_cmd = mock.MagicMock(
            return_value=(0, 'CANCELLED by 12345\n', ''))
        result = runtime.get_job_status(object(), 'cluster', returncode=None)
        assert result == (job_lib.JobStatus.CANCELLED, None)

    def test_unknown_state_returns_none(self, runtime, patched_target):
        """Unknown Slurm state → None (caller defers).

        ``_slurm_state_to_job_status`` returns None for unknown states;
        the runtime's get_job_status returns None so the caller defers.
        """
        patched_target.get_job_state = mock.MagicMock(
            return_value='SOME_FUTURE_STATE')
        result = runtime.get_job_status(object(), 'cluster', returncode=None)
        assert result is None

    def test_query_failure_surfaces_error(self, runtime, patched_target):
        """A Slurm query failure surfaces as ``(None, 'Slurm query ...')``.

        So the caller can log it without crashing the controller.
        """
        patched_target.get_job_state = mock.MagicMock(
            side_effect=RuntimeError('ssh down'))
        result = runtime.get_job_status(object(), 'cluster', returncode=None)
        assert result is not None
        status, msg = result
        assert status is None
        assert 'Slurm query error' in msg
        assert 'ssh down' in msg
