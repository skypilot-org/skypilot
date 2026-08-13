"""Unit tests for sky.provision.slurm.instance."""
import asyncio
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.slurm import instance
from sky.utils import status_lib

_CLUSTER = 'test-cluster'
_PROVIDER_CONFIG = {
    'ssh': {
        'hostname': 'localhost',
        'port': '22',
        'user': 'testuser',
        'private_key': '/path/to/key',
    }
}


@pytest.fixture
def mock_client(monkeypatch):
    """Mock SlurmClient for terminate_instances tests (SSH path)."""
    client = mock.MagicMock()
    monkeypatch.setattr(instance.slurm, 'SlurmClient',
                        mock.MagicMock(return_value=client))
    monkeypatch.setattr(instance.slurm_utils, 'is_inside_slurm_cluster',
                        mock.MagicMock(return_value=False))
    # Make waits resolve quickly in tests that exercise timeouts.
    monkeypatch.setattr(instance, '_TERMINATION_GRACE_PERIOD_SECONDS', 0.05)
    monkeypatch.setattr(instance, '_JOB_TERMINATION_TIMEOUT_SECONDS', 0.05)
    monkeypatch.setattr(instance, 'POLL_INTERVAL_SECONDS', 0.01)
    return client


class TestTerminateInstances:
    """Test terminate_instances() cancellation and escalation logic."""

    @pytest.mark.parametrize('job_state', [
        'COMPLETED',
        'CANCELLED',
        'FAILED',
        'TIMEOUT',
        'NODE_FAIL',
        'PREEMPTED',
        'SPECIAL_EXIT',
        'COMPLETING',
    ])
    def test_terminal_or_completing_state_no_cancel(self, mock_client,
                                                    job_state):
        mock_client.get_jobs_state_by_name.return_value = [job_state]
        instance.terminate_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)
        mock_client.cancel_jobs_by_name.assert_not_called()

    def test_no_jobs_found_no_cancel(self, mock_client):
        mock_client.get_jobs_state_by_name.return_value = []
        instance.terminate_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)
        mock_client.cancel_jobs_by_name.assert_not_called()

    @pytest.mark.parametrize('job_state', ['PENDING', 'CONFIGURING'])
    def test_pending_cancels_without_signal(self, mock_client, job_state):
        mock_client.get_jobs_state_by_name.return_value = [job_state]
        instance.terminate_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)
        mock_client.cancel_jobs_by_name.assert_called_once_with(_CLUSTER,
                                                                signal=None)

    @pytest.mark.parametrize('job_state', ['STAGING_OUT', 'SIGNALING'])
    def test_transient_state_single_graceful_cancel(self, mock_client,
                                                    job_state):
        # Transient states get the graceful signal but no verify/escalate.
        mock_client.get_jobs_state_by_name.return_value = [job_state]
        instance.terminate_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)
        mock_client.cancel_jobs_by_name.assert_called_once_with(_CLUSTER,
                                                                signal='TERM',
                                                                full=True)
        # No polling: only the initial state query.
        assert mock_client.get_jobs_state_by_name.call_count == 1

    def test_autodown_inside_cluster_single_graceful_cancel(
            self, mock_client, monkeypatch):
        # Inside the cluster (autodown), the Skylet performing the teardown
        # runs inside a job step, so no step-level TERM and no
        # verify/escalate.
        monkeypatch.setattr(instance.slurm_utils, 'is_inside_slurm_cluster',
                            mock.MagicMock(return_value=True))
        mock_client.get_jobs_state_by_name.return_value = ['RUNNING']
        instance.terminate_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)
        mock_client.cancel_jobs_by_name.assert_called_once_with(_CLUSTER,
                                                                signal='TERM',
                                                                full=True)
        assert mock_client.get_jobs_state_by_name.call_count == 1

    @pytest.mark.parametrize('job_state', ['RUNNING', 'SUSPENDED'])
    def test_graceful_termination_succeeds(self, mock_client, job_state):
        # The job exits (gone from squeue) within the grace period.
        mock_client.get_jobs_state_by_name.side_effect = [[job_state], []]
        instance.terminate_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)
        assert mock_client.cancel_jobs_by_name.call_args_list == [
            mock.call(_CLUSTER, signal='TERM'),
            mock.call(_CLUSTER, signal='TERM', full=True),
        ]

    def test_graceful_wait_tolerates_transient_query_failure(self, mock_client):
        # One failed poll inside the wait must not fail the teardown.
        mock_client.get_jobs_state_by_name.side_effect = [
            ['RUNNING'],
            exceptions.CommandError(255, 'squeue', 'ssh dropped', None),
            [],
        ]
        instance.terminate_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)
        assert mock_client.cancel_jobs_by_name.call_args_list == [
            mock.call(_CLUSTER, signal='TERM'),
            mock.call(_CLUSTER, signal='TERM', full=True),
        ]

    def test_escalates_when_job_survives_term(self, mock_client):
        # The job stays RUNNING through the grace period (e.g. a step
        # survived the TERM and blocks the batch script from exiting), then
        # exits after the plain scancel.
        def get_states(job_name):
            del job_name
            plain_scancel_issued = mock.call(_CLUSTER) in (
                mock_client.cancel_jobs_by_name.call_args_list)
            return [] if plain_scancel_issued else ['RUNNING']

        mock_client.get_jobs_state_by_name.side_effect = get_states
        instance.terminate_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)
        assert mock_client.cancel_jobs_by_name.call_args_list == [
            mock.call(_CLUSTER, signal='TERM'),
            mock.call(_CLUSTER, signal='TERM', full=True),
            mock.call(_CLUSTER),
        ]

    def test_raises_when_escalation_fails(self, mock_client):
        # The job keeps RUNNING even after the plain scancel.
        mock_client.get_jobs_state_by_name.return_value = ['RUNNING']
        with pytest.raises(RuntimeError, match='still.*running'):
            instance.terminate_instances(_CLUSTER,
                                         provider_config=_PROVIDER_CONFIG)
        assert mock.call(_CLUSTER) in (
            mock_client.cancel_jobs_by_name.call_args_list)

    def test_raises_when_job_wedges_in_completing(self, mock_client):
        # COMPLETING satisfies the grace-period wait (Slurm is tearing the
        # job down), but after escalation a job wedged in COMPLETING still
        # holds the allocation and must be reported, not treated as
        # success.
        def get_states(job_name):
            del job_name
            plain_scancel_issued = mock.call(_CLUSTER) in (
                mock_client.cancel_jobs_by_name.call_args_list)
            return ['COMPLETING'] if plain_scancel_issued else ['RUNNING']

        mock_client.get_jobs_state_by_name.side_effect = get_states
        with pytest.raises(RuntimeError, match='still.*running'):
            instance.terminate_instances(_CLUSTER,
                                         provider_config=_PROVIDER_CONFIG)


class TestWaitForJobStates:
    """Test _wait_for_job_states()."""

    def test_gone_from_queue(self):
        client = mock.MagicMock()
        client.get_jobs_state_by_name.return_value = []
        assert instance._wait_for_job_states(client,
                                             'job',
                                             instance._EXITING_JOB_STATES,
                                             timeout=1)

    def test_completing_counts_as_exiting(self):
        client = mock.MagicMock()
        client.get_jobs_state_by_name.return_value = ['COMPLETING']
        assert instance._wait_for_job_states(client,
                                             'job',
                                             instance._EXITING_JOB_STATES,
                                             timeout=1)

    def test_completing_is_not_terminal(self):
        client = mock.MagicMock()
        client.get_jobs_state_by_name.return_value = ['COMPLETING']
        assert not instance._wait_for_job_states(
            client, 'job', instance._TERMINAL_JOB_STATES, timeout=0)

    def test_running_times_out(self):
        client = mock.MagicMock()
        client.get_jobs_state_by_name.return_value = ['RUNNING']
        assert not instance._wait_for_job_states(
            client, 'job', instance._EXITING_JOB_STATES, timeout=0)

    def test_query_failure_until_deadline_returns_false(self):
        client = mock.MagicMock()
        client.get_jobs_state_by_name.side_effect = exceptions.CommandError(
            255, 'squeue', 'ssh dropped', None)
        assert not instance._wait_for_job_states(
            client, 'job', instance._EXITING_JOB_STATES, timeout=0)


class TestQueryInstances:
    """Test query_instances() missing-job safeguards."""

    def test_retries_missing_job(self, mock_client, monkeypatch):
        running_queries = 0

        def query_jobs(job_name, state_filters):
            nonlocal running_queries
            assert job_name == _CLUSTER
            if state_filters == ['running']:
                running_queries += 1
                if running_queries == 2:
                    return ['386700']
            return []

        mock_client.query_jobs.side_effect = query_jobs
        mock_client.get_job_nodes.return_value = (['node-a'], None)
        sleep = mock.MagicMock()
        monkeypatch.setattr(instance.time, 'sleep', sleep)

        result = instance.query_instances(_CLUSTER,
                                          _CLUSTER,
                                          provider_config=_PROVIDER_CONFIG,
                                          retry_if_missing=True)

        assert result == {
            'job386700-node-a': (status_lib.ClusterStatus.UP, None)
        }
        assert running_queries == 2
        sleep.assert_called_once_with(
            instance._QUERY_INSTANCES_RETRY_INTERVAL_SECONDS)

    def test_does_not_retry_by_default(self, mock_client, monkeypatch):
        mock_client.query_jobs.return_value = []
        sleep = mock.MagicMock()
        monkeypatch.setattr(instance.time, 'sleep', sleep)

        result = instance.query_instances(_CLUSTER,
                                          _CLUSTER,
                                          provider_config=_PROVIDER_CONFIG,
                                          retry_if_missing=False)

        assert not result
        assert mock_client.query_jobs.call_count == 7
        sleep.assert_not_called()

    def test_retry_exhaustion_returns_empty(self, mock_client, monkeypatch):
        mock_client.query_jobs.return_value = []
        monkeypatch.setattr(instance.time, 'sleep', mock.MagicMock())

        result = instance.query_instances(_CLUSTER,
                                          _CLUSTER,
                                          provider_config=_PROVIDER_CONFIG,
                                          retry_if_missing=True)

        assert not result
        expected_rounds = 1 + instance._MAX_QUERY_INSTANCES_RETRIES
        assert mock_client.query_jobs.call_count == 7 * expected_rounds

    def test_retry_observes_request_cancellation(self, mock_client,
                                                 monkeypatch):
        mock_client.query_jobs.return_value = []
        monkeypatch.setattr(instance.context_utils, 'raise_if_canceled',
                            mock.MagicMock(side_effect=asyncio.CancelledError))

        with pytest.raises(asyncio.CancelledError):
            instance.query_instances(_CLUSTER,
                                     _CLUSTER,
                                     provider_config=_PROVIDER_CONFIG,
                                     retry_if_missing=True)

        # Only the initial full status query ran; no empty result was returned.
        assert mock_client.query_jobs.call_count == 7
