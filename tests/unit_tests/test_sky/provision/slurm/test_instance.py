"""Unit tests for sky.provision.slurm.instance."""
from unittest import mock

import pytest

from sky.provision.slurm import instance


@pytest.fixture
def mock_client(monkeypatch):
    """Mock SlurmClient and run terminate_instances from inside the cluster.

    Running "inside" the cluster skips SSH config handling, so tests only
    exercise the cancellation logic.
    """
    client = mock.MagicMock()
    monkeypatch.setattr(instance.slurm, 'SlurmClient',
                        mock.MagicMock(return_value=client))
    monkeypatch.setattr(instance.slurm_utils, 'is_inside_slurm_cluster',
                        mock.MagicMock(return_value=True))
    # Make waits resolve quickly in tests that exercise timeouts.
    monkeypatch.setattr(instance, '_TERMINATION_GRACE_PERIOD_SECONDS', 0.05)
    monkeypatch.setattr(instance, '_JOB_TERMINATION_TIMEOUT_SECONDS', 0.05)
    monkeypatch.setattr(instance, 'POLL_INTERVAL_SECONDS', 0.01)
    return client


class TestTerminateInstances:
    """Test terminate_instances() cancellation and escalation logic."""

    def test_terminal_state_no_cancel(self, mock_client):
        mock_client.get_jobs_state_by_name.return_value = ['COMPLETED']
        instance.terminate_instances('cluster', provider_config={})
        mock_client.cancel_jobs_by_name.assert_not_called()

    def test_pending_cancels_without_signal(self, mock_client):
        mock_client.get_jobs_state_by_name.return_value = ['PENDING']
        instance.terminate_instances('cluster', provider_config={})
        mock_client.cancel_jobs_by_name.assert_called_once_with('cluster',
                                                                signal=None)

    def test_running_graceful_termination_succeeds(self, mock_client):
        # RUNNING at first, then the job exits (gone from squeue).
        mock_client.get_jobs_state_by_name.side_effect = [['RUNNING'], []]
        instance.terminate_instances('cluster', provider_config={})
        assert mock_client.cancel_jobs_by_name.call_args_list == [
            mock.call('cluster', signal='TERM'),
            mock.call('cluster', signal='TERM', full=True),
        ]

    def test_running_escalates_when_job_survives_term(self, mock_client):
        # The job stays RUNNING through the grace period (e.g. a step
        # survived the TERM and blocks the batch script from exiting), then
        # exits after the plain scancel.
        def get_states(job_name):
            del job_name
            plain_scancel_issued = mock.call('cluster') in (
                mock_client.cancel_jobs_by_name.call_args_list)
            return ['COMPLETING'] if plain_scancel_issued else ['RUNNING']

        mock_client.get_jobs_state_by_name.side_effect = get_states
        instance.terminate_instances('cluster', provider_config={})
        assert mock_client.cancel_jobs_by_name.call_args_list == [
            mock.call('cluster', signal='TERM'),
            mock.call('cluster', signal='TERM', full=True),
            mock.call('cluster'),
        ]

    def test_running_raises_when_escalation_fails(self, mock_client):
        # First call is the state check in terminate_instances; all
        # subsequent polls keep returning RUNNING.
        mock_client.get_jobs_state_by_name.return_value = ['RUNNING']
        with pytest.raises(RuntimeError, match='still.*running'):
            instance.terminate_instances('cluster', provider_config={})
        assert mock.call('cluster') in (
            mock_client.cancel_jobs_by_name.call_args_list)


class TestWaitForJobToExit:
    """Test _wait_for_job_to_exit()."""

    def test_gone_from_queue(self):
        client = mock.MagicMock()
        client.get_jobs_state_by_name.return_value = []
        assert instance._wait_for_job_to_exit(client, 'job', timeout=1)

    def test_completing_counts_as_exiting(self):
        client = mock.MagicMock()
        client.get_jobs_state_by_name.return_value = ['COMPLETING']
        assert instance._wait_for_job_to_exit(client, 'job', timeout=1)

    def test_running_times_out(self):
        client = mock.MagicMock()
        client.get_jobs_state_by_name.return_value = ['RUNNING']
        assert not instance._wait_for_job_to_exit(client, 'job', timeout=0)
