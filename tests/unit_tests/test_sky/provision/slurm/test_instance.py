"""Unit tests for sky.provision.slurm.instance."""
import json
import os
import shlex
import subprocess
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.slurm import instance
from sky.skylet import constants as skylet_constants
from sky.utils import command_runner

_CLUSTER = 'test-cluster'
_PROVIDER_CONFIG = {
    'ssh': {
        'hostname': 'localhost',
        'port': '22',
        'user': 'testuser',
        'private_key': '/path/to/key',
    }
}
_CONTAINER_IMAGE = 'ubuntu:24.04'
_CONTAINER_PROVIDER_CONFIG = {
    **_PROVIDER_CONFIG,
    'container_image': _CONTAINER_IMAGE,
}
_SNAPSHOT_GENERATION = '0123456789abcdef0123456789abcdef'
_NEW_SNAPSHOT_GENERATION = 'fedcba9876543210fedcba9876543210'


def _snapshot_manifest(num_nodes=2, generation=_SNAPSHOT_GENERATION):
    return {
        'version': instance.SNAPSHOT_MANIFEST_VERSION,
        'generation': generation,
        'image_id': 'ubuntu:24.04',
        'created_at': 1234.5,
        'has_job_db': False,
        'nodes': [f'node-{rank}' for rank in range(num_nodes)],
    }


class TestSnapshotManifest:
    """Tests snapshot manifest parsing and validation."""

    def test_valid_manifest(self):
        manifest = _snapshot_manifest()
        assert instance._validate_snapshot_manifest(
            manifest, expected_num_nodes=2) is manifest

    def test_num_nodes_mismatch(self):
        with pytest.raises(RuntimeError, match='node count does not match'):
            instance._validate_snapshot_manifest(_snapshot_manifest(),
                                                 expected_num_nodes=1)

    def test_invalid_source_nodes(self):
        manifest = _snapshot_manifest()
        manifest['nodes'][1] = ''
        with pytest.raises(RuntimeError, match='source node list'):
            instance._validate_snapshot_manifest(manifest)

    def test_invalid_generation(self):
        manifest = _snapshot_manifest()
        manifest['generation'] = '../other'
        with pytest.raises(RuntimeError, match='invalid generation'):
            instance._validate_snapshot_manifest(manifest)

    def test_missing_manifest(self):
        runner = mock.MagicMock()
        runner.run.return_value = (44, '', '')
        assert instance._read_snapshot_manifest(
            runner, '/home/test/.sky_snapshots/test-cluster') is None

    def test_read_valid_manifest(self):
        manifest = _snapshot_manifest()
        runner = mock.MagicMock()
        runner.run.return_value = (0, json.dumps(manifest), '')
        assert instance._read_snapshot_manifest(
            runner, '/home/test/.sky_snapshots/test-cluster') == manifest

    def test_read_corrupt_manifest(self):
        runner = mock.MagicMock()
        runner.run.return_value = (0, '{', '')
        with pytest.raises(RuntimeError, match='not valid JSON'):
            instance._read_snapshot_manifest(
                runner, '/home/test/.sky_snapshots/test-cluster')

    def test_manifest_paths_derived_from_generation(self):
        snapshot_dir = '/home/test/.sky_snapshots/test-cluster'
        manifest = _snapshot_manifest()
        manifest['has_job_db'] = True
        generation_dir = (f'{snapshot_dir}/generations/{_SNAPSHOT_GENERATION}')
        rank_paths, job_db_path = instance._manifest_paths(
            snapshot_dir, manifest)
        assert rank_paths == [
            f'{generation_dir}/rank0.sqsh',
            f'{generation_dir}/rank1.sqsh',
        ]
        assert job_db_path == f'{generation_dir}/jobs.db'

    def test_missing_rank_snapshot(self):
        runner = mock.MagicMock()
        runner.run.side_effect = [(0, '', ''), (1, '', '')]
        with pytest.raises(RuntimeError, match='rank 1'):
            instance._validate_snapshot_files(
                runner, '/home/test/.sky_snapshots/test-cluster',
                _snapshot_manifest())

    def test_missing_job_db_snapshot(self):
        manifest = _snapshot_manifest()
        manifest['has_job_db'] = True
        runner = mock.MagicMock()
        runner.run.side_effect = [(0, '', ''), (0, '', ''), (1, '', '')]
        with pytest.raises(RuntimeError, match='missing job database'):
            instance._validate_snapshot_files(
                runner, '/home/test/.sky_snapshots/test-cluster', manifest)

    def test_local_runner_publishes_without_rsync(self, tmp_path):
        """Inside-cluster publication writes the manifest directly.

        The head node's host is not guaranteed to have rsync, so the local
        runner path must not go through it.
        """
        runner = command_runner.LocalProcessCommandRunner()
        rsync = mock.MagicMock(wraps=runner.rsync)
        runner.rsync = rsync
        manifest = _snapshot_manifest()

        instance._write_snapshot_manifest(runner, str(tmp_path), manifest)

        rsync.assert_not_called()
        published = json.loads((tmp_path / 'manifest.json').read_text())
        assert published == manifest


class TestResolveSkyBaseDir:
    """Tests persisted Slurm cluster state directory lookup."""

    def test_uses_persisted_provider_value(self, monkeypatch):
        client = mock.MagicMock()
        get_config = mock.MagicMock(
            side_effect=AssertionError('global config must not be read'))
        monkeypatch.setattr(instance.skypilot_config,
                            'get_effective_region_config', get_config)
        provider_config = {
            **_PROVIDER_CONFIG,
            'sky_base_dir': '/old/shared/path',
        }

        result = instance._resolve_sky_base_dir(client, provider_config)

        assert result == '/old/shared/path'
        get_config.assert_not_called()
        client.get_env.assert_not_called()
        client.get_remote_home_dir.assert_not_called()

    def test_provider_without_value_resolves_current_config(self, monkeypatch):
        client = mock.MagicMock()
        resolve = mock.MagicMock(return_value='/current/shared/path')
        monkeypatch.setattr(instance.slurm_utils, 'resolve_sky_base_dir',
                            resolve)
        provider_config = {
            **_PROVIDER_CONFIG,
            'cluster': 'test-slurm',
        }

        result = instance._resolve_sky_base_dir(client, provider_config)

        assert result == '/current/shared/path'
        resolve.assert_called_once_with('test-slurm', client)


class TestResolveSkyPilotRuntimeDir:
    """Tests inside-cluster runtime directory lookup."""

    def test_inside_cluster_requires_runtime_dir_env(self, monkeypatch):
        monkeypatch.delenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                           raising=False)
        get_cluster = mock.MagicMock(
            side_effect=AssertionError('config fallback must not run'))
        monkeypatch.setattr(instance.slurm_utils,
                            'get_slurm_cluster_from_config', get_cluster)
        client = mock.MagicMock()

        with pytest.raises(RuntimeError, match='SKY_RUNTIME_DIR is not set'):
            instance._resolve_skypilot_runtime_dir(client,
                                                   _PROVIDER_CONFIG,
                                                   _CLUSTER,
                                                   inside_slurm_cluster=True)

        get_cluster.assert_not_called()
        client.get_env.assert_not_called()


@pytest.fixture
def mock_client(monkeypatch):
    """Mock SlurmClient for terminate_instances tests (SSH path)."""
    client = mock.MagicMock()
    monkeypatch.setattr(instance.slurm, 'SlurmClient',
                        mock.MagicMock(return_value=client))
    monkeypatch.setattr(instance.slurm_utils, 'is_inside_slurm_cluster',
                        mock.MagicMock(return_value=False))
    login_runner = mock.MagicMock()
    login_runner.run.return_value = (0, '', '')
    monkeypatch.setattr(instance, '_make_login_node_runner',
                        mock.MagicMock(return_value=login_runner))
    monkeypatch.setattr(instance, '_resolve_sky_base_dir',
                        mock.MagicMock(return_value='/home/test'))
    client.test_login_runner = login_runner
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
        remove_commands = [
            call.args[0]
            for call in mock_client.test_login_runner.run.call_args_list
        ]
        assert any('.sky_snapshots/test-cluster' in command
                   for command in remove_commands)

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

    def test_pre_batch_cleanup_runs_between_step_and_batch_term(
            self, monkeypatch):
        client = mock.MagicMock()
        client.get_jobs_state_by_name.return_value = ['RUNNING']
        monkeypatch.setattr(instance, '_wait_for_job_states',
                            mock.MagicMock(return_value=True))
        events = []

        def cancel(job_name, signal=None, full=False):
            assert job_name == _CLUSTER
            events.append((signal, full))

        client.cancel_jobs_by_name.side_effect = cancel

        instance._cancel_slurm_job(
            client,
            _CLUSTER,
            inside_slurm_cluster=False,
            pre_batch_cancel=lambda: events.append('cleanup'))

        assert events == [('TERM', False), 'cleanup', ('TERM', True)]

    def test_cleanup_failure_still_cancels_allocation(self, mock_client,
                                                      monkeypatch):
        mock_client.query_jobs.return_value = ['123']
        mock_client.get_job_nodes.return_value = (['node-a'], ['10.0.0.1'])
        mock_client.get_jobs_state_by_name.side_effect = [['RUNNING'], []]
        cleanup = mock.MagicMock(side_effect=RuntimeError('cleanup failed'))
        monkeypatch.setattr(instance, '_cleanup_slurm_allocation', cleanup)
        warning = mock.MagicMock()
        monkeypatch.setattr(instance.logger, 'warning', warning)

        instance.terminate_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)

        cleanup.assert_called_once()
        assert mock_client.cancel_jobs_by_name.call_args_list == [
            mock.call(_CLUSTER, signal='TERM'),
            mock.call(_CLUSTER, signal='TERM', full=True),
        ]
        warning.assert_called_once()
        assert 'cleanup failed' in warning.call_args.args[0]

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


class TestDrainSlurmWorkloadSteps:
    """Tests the pre-snapshot Slurm step drain."""

    @staticmethod
    def _patch_clock(monkeypatch):
        clock = mock.MagicMock()
        elapsed = [0.0]
        clock.monotonic.side_effect = lambda: elapsed[0]

        def sleep(seconds):
            elapsed[0] += seconds

        clock.sleep.side_effect = sleep
        monkeypatch.setattr(instance.time, 'monotonic', clock.monotonic)
        monkeypatch.setattr(instance.time, 'sleep', clock.sleep)
        monkeypatch.setattr(instance, 'POLL_INTERVAL_SECONDS', 1)
        monkeypatch.setattr(instance,
                            '_WORKLOAD_STEP_TERM_GRACE_PERIOD_SECONDS', 1)
        monkeypatch.setattr(instance, '_WORKLOAD_STEP_DRAIN_TIMEOUT_SECONDS', 3)
        return clock

    def test_preserves_only_infrastructure_steps(self):
        client = mock.MagicMock()
        client.list_job_steps.return_value = [
            instance.slurm.JobStepInfo('123.batch', 'batch'),
            instance.slurm.JobStepInfo('123.extern', 'extern'),
            instance.slurm.JobStepInfo('123.0', 'sky-container-keeper'),
            instance.slurm.JobStepInfo('123.1', 'sky-skylet-keeper'),
        ]

        instance._drain_slurm_workload_steps(client, '123')

        client.signal_job_step.assert_not_called()

    def test_terms_task_and_ssh_steps_then_waits_for_exit(self, monkeypatch):
        self._patch_clock(monkeypatch)
        client = mock.MagicMock()
        workload_steps = [
            instance.slurm.JobStepInfo('123.2', 'sky-1'),
            instance.slurm.JobStepInfo('123.3', 'bash'),
        ]
        client.list_job_steps.side_effect = [workload_steps, []]

        instance._drain_slurm_workload_steps(client, '123')

        assert client.signal_job_step.call_args_list == [
            mock.call('123', '123.2', 'TERM'),
            mock.call('123', '123.3', 'TERM'),
        ]

    def test_escalates_surviving_step_to_kill(self, monkeypatch):
        self._patch_clock(monkeypatch)
        client = mock.MagicMock()
        workload_step = instance.slurm.JobStepInfo('123.2', 'sky-1')
        killed = [False]
        client.list_job_steps.side_effect = lambda job_id: ([] if killed[0] else
                                                            [workload_step])

        def signal(job_id, step_id, signal_name):
            del job_id, step_id
            if signal_name == 'KILL':
                killed[0] = True

        client.signal_job_step.side_effect = signal

        instance._drain_slurm_workload_steps(client, '123')

        assert client.signal_job_step.call_args_list == [
            mock.call('123', '123.2', 'TERM'),
            mock.call('123', '123.2', 'KILL'),
        ]

    def test_fails_if_step_survives_kill(self, monkeypatch):
        self._patch_clock(monkeypatch)
        client = mock.MagicMock()
        client.list_job_steps.return_value = [
            instance.slurm.JobStepInfo('123.2', 'bash')
        ]

        with pytest.raises(RuntimeError, match=r'123\.2 \(bash\)'):
            instance._drain_slurm_workload_steps(client, '123')

        assert client.signal_job_step.call_args_list == [
            mock.call('123', '123.2', 'TERM'),
            mock.call('123', '123.2', 'KILL'),
        ]


class TestStopInstances:
    """Tests Slurm container export and stop ordering."""

    @staticmethod
    def _setup(monkeypatch, nodes, inside=False):
        client = mock.MagicMock()
        client.query_jobs.return_value = ['123']
        client.get_job_nodes.return_value = (nodes, [
            f'10.0.0.{i + 1}' for i in range(len(nodes))
        ])
        client.list_job_steps.return_value = []
        login_runner = mock.MagicMock()

        def run(command, **kwargs):
            del command, kwargs
            return 0, '', ''

        login_runner.run.side_effect = run
        head_runner = mock.MagicMock()
        head_runner.run_driver.return_value = (0, '', '')
        make_client = mock.MagicMock(return_value=client)
        monkeypatch.setattr(instance, '_make_slurm_client', make_client)
        monkeypatch.setattr(instance, '_make_login_node_runner',
                            mock.MagicMock(return_value=login_runner))
        monkeypatch.setattr(instance, '_resolve_sky_base_dir',
                            mock.MagicMock(return_value='/home/test'))
        if inside:
            # Inside the cluster, stop runs from the head node's skylet: the
            # factory must build the local client/runner instead of SSH ones.
            monkeypatch.setattr(instance.slurm_utils, 'is_inside_slurm_cluster',
                                mock.MagicMock(return_value=True))
            slurm_client_factory = mock.MagicMock(return_value=client)
            monkeypatch.setattr(instance.slurm, 'SlurmClient',
                                slurm_client_factory)
            monkeypatch.setattr(instance.command_runner,
                                'LocalProcessCommandRunner',
                                mock.MagicMock(return_value=login_runner))
            monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                               '/tmp/test-cluster')
            client.test_slurm_client_factory = slurm_client_factory
        else:
            monkeypatch.setattr(instance.slurm_utils, 'is_inside_slurm_cluster',
                                mock.MagicMock(return_value=False))
        client.test_make_client = make_client
        read_manifest = mock.MagicMock(return_value=None)
        monkeypatch.setattr(instance, '_read_snapshot_manifest', read_manifest)
        new_uuid = mock.MagicMock()
        new_uuid.hex = _NEW_SNAPSHOT_GENERATION
        monkeypatch.setattr(instance.uuid, 'uuid4',
                            mock.MagicMock(return_value=new_uuid))
        monkeypatch.setattr(instance, 'get_cluster_info', mock.MagicMock())
        monkeypatch.setattr(instance, 'get_command_runners',
                            mock.MagicMock(return_value=[head_runner]))
        write_manifest = mock.MagicMock()
        cancel_slurm_job = mock.MagicMock()
        monkeypatch.setattr(instance, '_write_snapshot_manifest',
                            write_manifest)
        monkeypatch.setattr(instance, '_cancel_slurm_job', cancel_slurm_job)
        monkeypatch.setattr(instance.slurm_utils,
                            'get_slurm_cluster_from_config',
                            mock.MagicMock(return_value='test-slurm'))
        client.test_read_manifest = read_manifest
        return (client, login_runner, head_runner, write_manifest,
                cancel_slurm_job)

    def test_drains_steps_before_snapshotting_job_database(self, monkeypatch):
        client, login_runner, head_runner, _, _ = self._setup(
            monkeypatch, ['node-a'])
        events = []
        head_runner.run_driver.side_effect = lambda *args, **kwargs: (
            events.append('cancel jobs') or (0, '', ''))
        client.list_job_steps.side_effect = lambda job_id: (events.append(
            'drain steps') or [])
        original_run = login_runner.run.side_effect

        def run(command, **kwargs):
            if 'sqlite3.connect' in command:
                events.append('backup jobs db')
            return original_run(command, **kwargs)

        login_runner.run.side_effect = run

        instance.stop_instances(_CLUSTER,
                                provider_config=_CONTAINER_PROVIDER_CONFIG)

        assert events.index('cancel jobs') < events.index('drain steps')
        assert events.index('drain steps') < events.index('backup jobs db')

    def test_exports_all_nodes_then_publishes_manifest(self, monkeypatch):
        nodes = ['node-a', 'node-b']
        _, login_runner, head_runner, write_manifest, cancel_slurm_job = (
            self._setup(monkeypatch, nodes))

        instance.stop_instances(_CLUSTER,
                                provider_config=_CONTAINER_PROVIDER_CONFIG)

        driver_commands = [
            call.args[0] for call in head_runner.run_driver.call_args_list
        ]
        assert len(driver_commands) == 2
        assert driver_commands[0].startswith('test -f ')
        assert driver_commands[0].endswith('/skypilot-runtime/bin/activate')
        assert 'cancel_jobs_encoded_results' in driver_commands[1]
        head_runner.run.assert_not_called()
        stop_skylet_commands = [
            call.args[0]
            for call in login_runner.run.call_args_list
            if 'skylet_pid' in call.args[0]
        ]
        assert len(stop_skylet_commands) == 1
        stop_skylet_command = stop_skylet_commands[0]
        assert 'rm -f --' in stop_skylet_command
        assert (stop_skylet_command.index('.sky/skylet_start') <
                stop_skylet_command.index('.sky/skylet_pid'))
        manifest = write_manifest.call_args.args[2]
        assert manifest['generation'] == _NEW_SNAPSHOT_GENERATION
        assert manifest['nodes'] == nodes
        assert manifest['has_job_db'] is True
        export_commands = [
            call.args[0]
            for call in login_runner.run.call_args_list
            if 'enroot export' in call.args[0]
        ]
        assert len(export_commands) == 2
        assert all('enroot export -f' in command for command in export_commands)
        assert all(f'.staging-{_NEW_SNAPSHOT_GENERATION}' in command
                   for command in export_commands)
        assert any(
            '--nodelist=node-a' in command for command in export_commands)
        assert any(
            '--nodelist=node-b' in command for command in export_commands)
        backup_job_db_commands = [
            call.args[0]
            for call in login_runner.run.call_args_list
            if 'sqlite3.connect' in call.args[0]
        ]
        assert len(backup_job_db_commands) == 1
        backup_job_db_script = shlex.split(backup_job_db_commands[0])[-1]
        assert backup_job_db_script.startswith(
            'export SKY_RUNTIME_DIR=/tmp/test-cluster && ')
        assert skylet_constants.SKY_SLURM_PYTHON_CMD in backup_job_db_script
        assert '/usr/bin/python3 -c' not in backup_job_db_script
        cancel_slurm_job.assert_called_once()

    def test_cleanup_allocation_runs_on_every_node(self, monkeypatch):
        client = mock.MagicMock()
        login_runner = mock.MagicMock()
        login_runner.run.return_value = (0, '', '')
        monkeypatch.setattr(instance.skypilot_config,
                            'get_effective_region_config',
                            mock.MagicMock(return_value=None))
        monkeypatch.setattr(instance, '_resolve_sky_base_dir',
                            mock.MagicMock(return_value='/home/test'))
        monkeypatch.setattr(instance.slurm_utils,
                            'get_slurm_cluster_from_config',
                            mock.MagicMock(return_value='test-slurm'))

        instance._cleanup_slurm_allocation(client, login_runner, _CLUSTER,
                                           _PROVIDER_CONFIG, '123',
                                           ['node-a', 'node-b'])

        node_cleanup = login_runner.run.call_args_list[0].args[0]
        assert '--jobid=123' in node_cleanup
        assert '--nodes=2 --ntasks-per-node=1' in node_cleanup
        assert 'pyxis_test-cluster' in node_cleanup
        assert 'pyxis_123_test-cluster' in node_cleanup
        assert ('for ((attempt = 1; attempt <= 30; attempt++)); do\n'
                '            if ! container_exists "$enroot_name"; then'
                in node_cleanup)
        assert 'rm -rf -- /tmp/test-cluster' in node_cleanup
        shared_cleanup = login_runner.run.call_args_list[1].args[0]
        assert shared_cleanup == instance._remove_shared_state_script(
            '/home/test/.sky_clusters/test-cluster', preserve_logs=False)

    def test_remove_shared_state_script_preserves_logs(self):
        script = instance._remove_shared_state_script(
            '/home/test/.sky_clusters/test-cluster', preserve_logs=True)
        assert '! -name sky_logs' in script
        assert '-print -quit' in script
        # The verification must fail when find itself errors (e.g. a stale
        # file handle), not only when leftovers remain.
        assert '[ -z "$leftovers" ]' in script

    def test_remove_shared_state_script_retries_until_verified(self):
        script = instance._remove_shared_state_script(
            '/home/test/.sky_clusters/test-cluster', preserve_logs=False)
        assert script.count('sleep') == 1
        assert 'exit 0' in script
        assert 'exit 1' in script

    def test_cleanup_fails_after_exhausted_retries(self, monkeypatch, tmp_path):
        # A removal that keeps failing must not be reported as success.
        fake_bin = tmp_path / 'bin'
        fake_bin.mkdir()
        (fake_bin / 'rm').write_text('#!/bin/bash\nexit 1\n')
        os.chmod(fake_bin / 'rm', 0o755)
        home = tmp_path / '.sky_clusters' / _CLUSTER
        (home / 'sky_workdir').mkdir(parents=True)
        (home / 'sky_workdir' / 'file').write_text('state')
        env = {**os.environ, 'PATH': f'{fake_bin}:{os.environ["PATH"]}'}
        monkeypatch.setattr(instance,
                            '_SHARED_STATE_CLEANUP_RETRY_INTERVAL_SECONDS', 0)
        script = instance._remove_shared_state_script(str(home),
                                                      preserve_logs=False)

        result = subprocess.run(['/bin/bash', '-c', script],
                                check=False,
                                capture_output=True,
                                text=True,
                                env=env)

        assert result.returncode == 1
        assert (home / 'sky_workdir' / 'file').exists()

    def test_cleanup_retries_stale_removal(self, monkeypatch, tmp_path):
        # A stale NFS handle fails the first rm and clears later: the cleanup
        # script must retry instead of leaving a half-deleted home behind.
        fake_bin = tmp_path / 'bin'
        fake_bin.mkdir()
        real_rm = subprocess.run(['/usr/bin/which', 'rm'],
                                 check=True,
                                 capture_output=True,
                                 text=True).stdout.strip()
        (fake_bin /
         'rm').write_text('#!/bin/bash\n'
                          'echo "$@" >> "$CLEANUP_LOG"\n'
                          '[ "$(wc -l < "$CLEANUP_LOG")" -ge 2 ] && exec ' +
                          real_rm + ' "$@"\n'
                          'exit 1\n')
        os.chmod(fake_bin / 'rm', 0o755)
        home = tmp_path / '.sky_clusters' / _CLUSTER
        (home / 'sky_workdir').mkdir(parents=True)
        (home / 'sky_workdir' / 'file').write_text('state')
        log_file = tmp_path / 'cleanup.log'
        log_file.touch()
        env = {
            **os.environ, 'PATH': f'{fake_bin}:{os.environ["PATH"]}',
            'CLEANUP_LOG': str(log_file)
        }
        monkeypatch.setattr(instance,
                            '_SHARED_STATE_CLEANUP_RETRY_INTERVAL_SECONDS', 0)
        script = instance._remove_shared_state_script(str(home),
                                                      preserve_logs=False)

        result = subprocess.run(['/bin/bash', '-c', script],
                                check=False,
                                capture_output=True,
                                text=True,
                                env=env)

        assert result.returncode == 0, result.stderr
        assert not home.exists()
        assert len(log_file.read_text().splitlines()) == 2

    def test_cleanup_allocation_preserves_logs(self, monkeypatch, tmp_path):
        client = mock.MagicMock()
        login_runner = mock.MagicMock()

        def run(command, **kwargs):
            del kwargs
            if command.startswith('srun '):
                return 0, '', ''
            result = subprocess.run(['/bin/bash', '-c', command],
                                    check=False,
                                    capture_output=True,
                                    text=True)
            return result.returncode, result.stdout, result.stderr

        login_runner.run.side_effect = run
        monkeypatch.setattr(instance.skypilot_config,
                            'get_effective_region_config',
                            mock.MagicMock(return_value=None))
        monkeypatch.setattr(instance, '_resolve_sky_base_dir',
                            mock.MagicMock(return_value=str(tmp_path)))
        monkeypatch.setattr(instance.slurm_utils,
                            'get_slurm_cluster_from_config',
                            mock.MagicMock(return_value='test-slurm'))
        cluster_home = tmp_path / '.sky_clusters' / _CLUSTER
        log_file = cluster_home / 'sky_logs' / '1-job' / 'run.log'
        log_file.parent.mkdir(parents=True)
        log_file.write_text('job output')
        stale_state = cluster_home / '.sky' / 'state'
        stale_state.parent.mkdir()
        stale_state.write_text('state')
        workdir = cluster_home / 'sky_workdir'
        workdir.mkdir()

        instance._cleanup_slurm_allocation(client,
                                           login_runner,
                                           _CLUSTER,
                                           _PROVIDER_CONFIG,
                                           '123', ['node-a'],
                                           preserve_logs=True)

        assert log_file.read_text() == 'job output'
        assert not stale_state.parent.exists()
        assert not workdir.exists()

    def test_stop_cleanup_preserves_logs(self, monkeypatch):
        _, _, _, _, cancel_slurm_job = self._setup(monkeypatch, ['node-a'])
        cleanup_slurm_allocation = mock.MagicMock()
        monkeypatch.setattr(instance, '_cleanup_slurm_allocation',
                            cleanup_slurm_allocation)

        instance.stop_instances(_CLUSTER,
                                provider_config=_CONTAINER_PROVIDER_CONFIG)
        cleanup = cancel_slurm_job.call_args.kwargs['pre_batch_cancel']
        cleanup()

        assert cleanup_slurm_allocation.call_args.kwargs == {
            'preserve_logs': True,
        }

    def test_stop_cleanup_failure_still_cancels_after_manifest(
            self, monkeypatch):
        cancel_slurm_job = instance._cancel_slurm_job
        client, _, _, write_manifest, _ = self._setup(monkeypatch, ['node-a'])
        monkeypatch.setattr(instance, '_cancel_slurm_job', cancel_slurm_job)
        client.get_jobs_state_by_name.side_effect = [['RUNNING'], []]
        events = []
        write_manifest.side_effect = lambda *args: events.append('manifest')

        def fail_cleanup(*args, **kwargs):
            del args, kwargs
            events.append('cleanup')
            raise RuntimeError('cleanup failed')

        cleanup = mock.MagicMock(side_effect=fail_cleanup)
        monkeypatch.setattr(instance, '_cleanup_slurm_allocation', cleanup)

        def cancel(job_name, signal=None, full=False):
            assert job_name == _CLUSTER
            events.append(('cancel', signal, full))

        client.cancel_jobs_by_name.side_effect = cancel
        warning = mock.MagicMock()
        monkeypatch.setattr(instance.logger, 'warning', warning)

        instance.stop_instances(_CLUSTER,
                                provider_config=_CONTAINER_PROVIDER_CONFIG)

        write_manifest.assert_called_once()
        cleanup.assert_called_once()
        assert events == [
            'manifest',
            ('cancel', 'TERM', False),
            'cleanup',
            ('cancel', 'TERM', True),
        ]
        warning.assert_called_once()
        assert 'cleanup failed' in warning.call_args.args[0]

    def test_stop_without_container_image_is_rejected(self, monkeypatch):
        _, _, head_runner, write_manifest, cancel_slurm_job = (self._setup(
            monkeypatch, ['node-a']))

        with pytest.raises(exceptions.NotSupportedError,
                           match='no container image'):
            instance.stop_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)

        # Nothing that touches the allocation may run: the cluster cannot
        # be snapshotted without persisted container metadata.
        head_runner.run_driver.assert_not_called()
        write_manifest.assert_not_called()
        cancel_slurm_job.assert_not_called()

    def test_missing_runtime_venv_skips_job_cancellation(self, monkeypatch):
        _, _, head_runner, write_manifest, cancel_slurm_job = (self._setup(
            monkeypatch, ['node-a']))
        head_runner.run_driver.side_effect = [(1, '', ''), (0, '', '')]
        warning = mock.MagicMock()
        monkeypatch.setattr(instance.logger, 'warning', warning)

        instance.stop_instances(_CLUSTER,
                                provider_config=_CONTAINER_PROVIDER_CONFIG)

        # The venv check ran, the cancel itself was skipped, and the stop
        # continued so the cluster can still be recovered.
        head_runner.run_driver.assert_called_once()
        assert head_runner.run_driver.call_args.args[0].startswith('test -f ')
        warning.assert_called_once()
        assert 'runtime venv missing' in warning.call_args.args[0]
        write_manifest.assert_called_once()
        cancel_slurm_job.assert_called_once()

    def test_missing_runtime_venv_skips_local_cancel(self, monkeypatch):
        _, local_runner, _, write_manifest, cancel_slurm_job = (self._setup(
            monkeypatch, ['node-a'], inside=True))

        def run(command, **kwargs):
            del kwargs
            if (command.startswith('test -f ') and
                    '/skypilot-runtime/bin/activate' in command):
                return 1, '', ''
            return 0, '', ''

        local_runner.run.side_effect = run
        warning = mock.MagicMock()
        monkeypatch.setattr(instance.logger, 'warning', warning)

        instance.stop_instances(_CLUSTER,
                                provider_config=_CONTAINER_PROVIDER_CONFIG)

        commands = [call.args[0] for call in local_runner.run.call_args_list]
        assert not any(
            'cancel_jobs_encoded_results' in command for command in commands)
        warning.assert_called_once()
        assert 'runtime venv missing' in warning.call_args.args[0]
        write_manifest.assert_called_once()
        cancel_slurm_job.assert_called_once()

    def test_export_failure_preserves_previous_snapshot(self, monkeypatch):
        client, login_runner, _, write_manifest, cancel_slurm_job = self._setup(
            monkeypatch, ['node-a', 'node-b'])
        previous_manifest = _snapshot_manifest()
        client.test_read_manifest.return_value = previous_manifest
        original_run = login_runner.run.side_effect

        def fail_rank_one(command, **kwargs):
            if 'enroot export' in command and 'rank1.sqsh' in command:
                return 7, '', 'export failed'
            return original_run(command, **kwargs)

        login_runner.run.side_effect = fail_rank_one

        with pytest.raises(exceptions.CommandError, match='rank 1'):
            instance.stop_instances(_CLUSTER,
                                    provider_config=_CONTAINER_PROVIDER_CONFIG)

        write_manifest.assert_not_called()
        cancel_slurm_job.assert_not_called()
        previous_generation_dir = instance._snapshot_generation_dir(
            '/home/test/.sky_snapshots/test-cluster',
            previous_manifest['generation'])
        commands = [call.args[0] for call in login_runner.run.call_args_list]
        assert not any(
            previous_generation_dir in command for command in commands)

    def test_previous_generation_removed_after_manifest_publish(
            self, monkeypatch):
        client, login_runner, _, write_manifest, _ = self._setup(
            monkeypatch, ['node-a'])
        previous_manifest = _snapshot_manifest(num_nodes=1)
        client.test_read_manifest.return_value = previous_manifest
        previous_generation_dir = instance._snapshot_generation_dir(
            '/home/test/.sky_snapshots/test-cluster',
            previous_manifest['generation'])
        original_run = login_runner.run.side_effect
        events = []

        def record_snapshot_events(command, **kwargs):
            if command.startswith('test ! -e ') and 'mv --' in command:
                events.append('commit generation')
            if command == f'rm -rf -- {previous_generation_dir}':
                events.append('remove previous generation')
            return original_run(command, **kwargs)

        login_runner.run.side_effect = record_snapshot_events
        write_manifest.side_effect = lambda *args: events.append(
            'publish manifest')

        instance.stop_instances(_CLUSTER,
                                provider_config=_CONTAINER_PROVIDER_CONFIG)

        assert events == [
            'commit generation',
            'publish manifest',
            'remove previous generation',
        ]

    def test_manifest_publish_failure_preserves_generations(self, monkeypatch):
        client, login_runner, _, write_manifest, cancel_slurm_job = self._setup(
            monkeypatch, ['node-a'])
        previous_manifest = _snapshot_manifest(num_nodes=1)
        client.test_read_manifest.return_value = previous_manifest
        write_manifest.side_effect = RuntimeError('publish failed')

        with pytest.raises(RuntimeError, match='publish failed'):
            instance.stop_instances(_CLUSTER,
                                    provider_config=_CONTAINER_PROVIDER_CONFIG)

        previous_generation_dir = instance._snapshot_generation_dir(
            '/home/test/.sky_snapshots/test-cluster',
            previous_manifest['generation'])
        new_generation_dir = instance._snapshot_generation_dir(
            '/home/test/.sky_snapshots/test-cluster', _NEW_SNAPSHOT_GENERATION)
        commands = [call.args[0] for call in login_runner.run.call_args_list]
        remove_commands = [
            command for command in commands if command.startswith('rm -rf -- ')
        ]
        assert not any(
            previous_generation_dir in command for command in remove_commands)
        assert not any(
            new_generation_dir in command for command in remove_commands)
        cancel_slurm_job.assert_not_called()

    def test_missing_container_preserves_existing_snapshot(self, monkeypatch):
        client, login_runner, _, write_manifest, cancel_slurm_job = self._setup(
            monkeypatch, ['node-a', 'node-b'])
        client.test_read_manifest.return_value = _snapshot_manifest()
        original_run = login_runner.run.side_effect

        def fail_node_preflight(command, **kwargs):
            if ('enroot list' in command and 'enroot export' not in command and
                    '--nodelist=node-b' in command):
                return 1, '', 'Pyxis container not found on node node-b'
            return original_run(command, **kwargs)

        login_runner.run.side_effect = fail_node_preflight

        with pytest.raises(exceptions.CommandError, match='node node-b'):
            instance.stop_instances(_CLUSTER,
                                    provider_config=_CONTAINER_PROVIDER_CONFIG)

        commands = [call.args[0] for call in login_runner.run.call_args_list]
        assert not any(
            command.startswith(
                'rm -rf -- /home/test/.sky_snapshots/test-cluster')
            for command in commands)
        write_manifest.assert_not_called()
        cancel_slurm_job.assert_not_called()

    def test_inside_cluster_uses_local_execution(self, monkeypatch):
        (client, local_runner, head_runner, write_manifest,
         cancel_slurm_job) = self._setup(monkeypatch, ['node-a'], inside=True)

        instance.stop_instances(_CLUSTER,
                                provider_config=_CONTAINER_PROVIDER_CONFIG)

        # No SSH to the login node: the local client is built directly.
        client.test_make_client.assert_not_called()
        client.test_slurm_client_factory.assert_called_once_with(
            is_inside_slurm_cluster=True)
        # The job-driver cancel runs locally instead of via srun over SSH.
        instance.get_command_runners.assert_not_called()
        head_runner.run_driver.assert_not_called()
        cancel_commands = [
            call.args[0]
            for call in local_runner.run.call_args_list
            if 'cancel_jobs_encoded_results' in call.args[0]
        ]
        assert len(cancel_commands) == 1
        assert cancel_commands[0].startswith(
            f'export {skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY}=/tmp/'
            'test-cluster && ')
        write_manifest.assert_called_once()
        cancel_slurm_job.assert_called_once()
        assert cancel_slurm_job.call_args.args[2] is True

    def test_inside_cluster_skips_skylet_kill(self, monkeypatch):
        _, local_runner, _, _, _ = self._setup(monkeypatch, ['node-a'],
                                               inside=True)

        instance.stop_instances(_CLUSTER,
                                provider_config=_CONTAINER_PROVIDER_CONFIG)

        # The skylet executing the stop is the process the skylet-kill step
        # would stop, so the stop flow must not touch its keeper spec or pid.
        commands = [call.args[0] for call in local_runner.run.call_args_list]
        assert not any('skylet_pid' in command for command in commands)
        assert not any('skylet_start' in command for command in commands)

    def test_inside_cluster_cancels_after_manifest_without_precleanup(
            self, monkeypatch):
        cancel_slurm_job = instance._cancel_slurm_job
        (client, local_runner, _, write_manifest, _) = self._setup(monkeypatch,
                                                                   ['node-a'],
                                                                   inside=True)
        monkeypatch.setattr(instance, '_cancel_slurm_job', cancel_slurm_job)
        client.get_jobs_state_by_name.return_value = ['RUNNING']
        events = []
        original_run = local_runner.run.side_effect

        def run(command, **kwargs):
            if 'cancel_jobs_encoded_results' in command:
                events.append('cancel jobs')
            if 'sqlite3.connect' in command:
                events.append('backup jobs db')
            if command.startswith('test ! -e ') and 'mv --' in command:
                events.append('commit generation')
            return original_run(command, **kwargs)

        local_runner.run.side_effect = run
        client.list_job_steps.side_effect = lambda job_id: (events.append(
            'drain steps') or [])
        write_manifest.side_effect = lambda *args: events.append(
            'publish manifest')
        cleanup = mock.MagicMock()
        monkeypatch.setattr(instance, '_cleanup_slurm_allocation', cleanup)

        def cancel(job_name, signal=None, full=False):
            assert job_name == _CLUSTER
            events.append(('cancel', signal, full))

        client.cancel_jobs_by_name.side_effect = cancel

        instance.stop_instances(_CLUSTER,
                                provider_config=_CONTAINER_PROVIDER_CONFIG)

        assert events == [
            'cancel jobs',
            'drain steps',
            'backup jobs db',
            'commit generation',
            'publish manifest',
            ('cancel', 'TERM', True),
        ]
        cleanup.assert_not_called()
        # Fire-and-forget: the only state query is the pre-cancel one.
        assert client.get_jobs_state_by_name.call_count == 1

    def test_inside_cluster_cancel_failure_preserves_runtime_state(
            self, monkeypatch):
        cancel_slurm_job = instance._cancel_slurm_job
        (client, local_runner, _, write_manifest, _) = self._setup(monkeypatch,
                                                                   ['node-a'],
                                                                   inside=True)
        monkeypatch.setattr(instance, '_cancel_slurm_job', cancel_slurm_job)
        client.get_jobs_state_by_name.return_value = ['RUNNING']
        client.cancel_jobs_by_name.side_effect = RuntimeError('scancel failed')
        cleanup = mock.MagicMock()
        monkeypatch.setattr(instance, '_cleanup_slurm_allocation', cleanup)

        with pytest.raises(RuntimeError, match='scancel failed'):
            instance.stop_instances(_CLUSTER,
                                    provider_config=_CONTAINER_PROVIDER_CONFIG)

        write_manifest.assert_called_once()
        cleanup.assert_not_called()
        commands = [call.args[0] for call in local_runner.run.call_args_list]
        assert not any(
            command == 'rm -rf -- /tmp/test-cluster' for command in commands)


class TestGetCommandRunners:
    """Container-ness must come from persisted config, not a filesystem probe."""

    @staticmethod
    def _cluster_info(provider_config):
        instance_info = instance.common.InstanceInfo(
            instance_id='123,node-a',
            internal_ip='10.0.0.1',
            external_ip='login.example.com',
            ssh_port=22,
            tags={
                instance.constants.TAG_SKYPILOT_CLUSTER_NAME: _CLUSTER,
                'job_id': '123',
                'node': 'node-a',
            },
            node_name='123,node-a')
        return instance.common.ClusterInfo(
            instances={'123,node-a': [instance_info]},
            head_instance_id='123,node-a',
            provider_name='slurm',
            provider_config=provider_config,
        )

    @staticmethod
    def _resolve_paths(monkeypatch, client):
        monkeypatch.setattr(instance.slurm, 'SlurmClient',
                            mock.MagicMock(return_value=client))
        monkeypatch.setattr(instance, '_resolve_sky_base_dir',
                            mock.MagicMock(return_value='/home/test'))
        monkeypatch.setattr(instance.skypilot_config,
                            'get_effective_region_config',
                            mock.MagicMock(return_value=None))
        monkeypatch.setattr(instance.slurm_utils,
                            'get_slurm_cluster_from_config',
                            mock.MagicMock(return_value='test-slurm'))

    @staticmethod
    def _provider_config(tmp_path, container: bool) -> dict:
        key = tmp_path / 'key'
        key.write_text('')
        config = {
            **_PROVIDER_CONFIG,
            'ssh': {
                **_PROVIDER_CONFIG['ssh'],
                'private_key': str(key),
            },
        }
        if container:
            config['container_image'] = _CONTAINER_IMAGE
        return config

    def test_container_cluster_gets_container_runners(self, monkeypatch,
                                                      tmp_path):
        self._resolve_paths(monkeypatch, mock.MagicMock())

        runners = instance.get_command_runners(
            self._cluster_info(self._provider_config(tmp_path, container=True)))

        assert len(runners) == 1
        assert runners[0].container_args is not None
        assert ':exec' in runners[0].container_args

    def test_non_container_cluster_runs_on_host(self, monkeypatch, tmp_path):
        self._resolve_paths(monkeypatch, mock.MagicMock())

        runners = instance.get_command_runners(
            self._cluster_info(self._provider_config(tmp_path,
                                                     container=False)))

        assert len(runners) == 1
        assert runners[0].container_args is None

    def test_false_filesystem_probe_does_not_cause_host_execution(
            self, monkeypatch, tmp_path):
        # A stale NFS lookup previously made this check return False for a
        # running container cluster, silently executing it on the host.
        client = mock.MagicMock()
        client.check_file_exists.return_value = False
        self._resolve_paths(monkeypatch, client)

        runners = instance.get_command_runners(
            self._cluster_info(self._provider_config(tmp_path, container=True)))

        client.check_file_exists.assert_not_called()
        assert runners[0].container_args is not None


class TestQueryInstances:
    """Tests manifest-backed Slurm stopped status."""

    def test_manifest_reports_each_rank_stopped(self, monkeypatch):
        client = mock.MagicMock()
        client.query_jobs.return_value = []
        monkeypatch.setattr(instance.slurm, 'SlurmClient',
                            mock.MagicMock(return_value=client))
        login_runner = mock.MagicMock()
        monkeypatch.setattr(instance, '_make_login_node_runner',
                            mock.MagicMock(return_value=login_runner))
        get_config = mock.MagicMock(
            side_effect=AssertionError('global config must not be read'))
        monkeypatch.setattr(instance.skypilot_config,
                            'get_effective_region_config', get_config)
        manifest = _snapshot_manifest(num_nodes=2)
        read_manifest = mock.MagicMock(return_value=manifest)
        monkeypatch.setattr(instance, '_read_snapshot_manifest', read_manifest)
        provider_config = {
            **_PROVIDER_CONFIG,
            'sky_base_dir': '/home/test',
        }

        statuses = instance.query_instances('test-cluster',
                                            _CLUSTER,
                                            provider_config=provider_config)

        assert statuses == {
            'snapshot-rank-0':
                (instance.status_lib.ClusterStatus.STOPPED, None),
            'snapshot-rank-1':
                (instance.status_lib.ClusterStatus.STOPPED, None),
        }
        read_manifest.assert_called_once_with(
            login_runner, '/home/test/.sky_snapshots/test-cluster')
        get_config.assert_not_called()

    def test_running_job_ignores_recent_terminal_allocation(self, monkeypatch):
        client = mock.MagicMock()

        def query_jobs(job_name, states):
            del job_name
            if states == ['running']:
                return ['new-job']
            if states == ['completed']:
                return ['old-job']
            return []

        client.query_jobs.side_effect = query_jobs
        client.get_job_nodes.return_value = (['node-a'], ['10.0.0.1'])
        client.get_job_reason.return_value = 'None'
        monkeypatch.setattr(instance.slurm, 'SlurmClient',
                            mock.MagicMock(return_value=client))
        read_manifest = mock.MagicMock()
        monkeypatch.setattr(instance, '_read_snapshot_manifest', read_manifest)

        statuses = instance.query_instances('test-cluster',
                                            _CLUSTER,
                                            provider_config=_PROVIDER_CONFIG,
                                            non_terminated_only=False)

        assert statuses == {
            instance.slurm_utils.instance_id('new-job', 'node-a'):
                (instance.status_lib.ClusterStatus.UP, None)
        }
        read_manifest.assert_not_called()
