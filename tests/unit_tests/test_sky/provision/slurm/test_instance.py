"""Unit tests for sky.provision.slurm.instance."""
import json
import shlex
import subprocess
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.slurm import instance
from sky.skylet import constants as skylet_constants

_CLUSTER = 'test-cluster'
_PROVIDER_CONFIG = {
    'ssh': {
        'hostname': 'localhost',
        'port': '22',
        'user': 'testuser',
        'private_key': '/path/to/key',
    }
}
_SNAPSHOT_GENERATION = '0123456789abcdef0123456789abcdef'
_NEW_SNAPSHOT_GENERATION = 'fedcba9876543210fedcba9876543210'


def _snapshot_manifest(snapshot_dir='/home/test/.sky_snapshots/test-cluster',
                       num_nodes=2,
                       generation=_SNAPSHOT_GENERATION):
    generation_dir = instance._snapshot_generation_dir(snapshot_dir, generation)
    return {
        'version': instance.SNAPSHOT_MANIFEST_VERSION,
        'generation': generation,
        'image_id': 'ubuntu:24.04',
        'num_nodes': num_nodes,
        'created_at': 1234.5,
        'job_db_path': None,
        'snapshots': [{
            'rank': rank,
            'node': f'node-{rank}',
            'path': instance._snapshot_rank_path(generation_dir, rank),
        } for rank in range(num_nodes)],
    }


class TestSnapshotManifest:
    """Tests snapshot manifest parsing and validation."""

    def test_valid_manifest(self):
        snapshot_dir = '/home/test/.sky_snapshots/test-cluster'
        manifest = _snapshot_manifest(snapshot_dir)
        assert instance._validate_snapshot_manifest(
            manifest, snapshot_dir, expected_num_nodes=2) is manifest

    def test_num_nodes_mismatch(self):
        snapshot_dir = '/home/test/.sky_snapshots/test-cluster'
        with pytest.raises(RuntimeError, match='node count does not match'):
            instance._validate_snapshot_manifest(
                _snapshot_manifest(snapshot_dir),
                snapshot_dir,
                expected_num_nodes=1)

    def test_rank_path_mismatch(self):
        snapshot_dir = '/home/test/.sky_snapshots/test-cluster'
        manifest = _snapshot_manifest(snapshot_dir)
        manifest['snapshots'][1]['path'] = '/other/rank1.sqsh'
        with pytest.raises(RuntimeError, match='path for rank 1'):
            instance._validate_snapshot_manifest(manifest, snapshot_dir)

    def test_invalid_generation(self):
        snapshot_dir = '/home/test/.sky_snapshots/test-cluster'
        manifest = _snapshot_manifest(snapshot_dir)
        manifest['generation'] = '../other'
        with pytest.raises(RuntimeError, match='invalid generation'):
            instance._validate_snapshot_manifest(manifest, snapshot_dir)

    def test_missing_manifest(self):
        runner = mock.MagicMock()
        runner.run.return_value = (44, '', '')
        assert instance._read_snapshot_manifest(
            runner, '/home/test/.sky_snapshots/test-cluster') is None

    def test_read_valid_manifest(self):
        snapshot_dir = '/home/test/.sky_snapshots/test-cluster'
        manifest = _snapshot_manifest(snapshot_dir)
        runner = mock.MagicMock()
        runner.run.return_value = (0, json.dumps(manifest), '')
        assert instance._read_snapshot_manifest(runner,
                                                snapshot_dir) == manifest

    def test_read_corrupt_manifest(self):
        runner = mock.MagicMock()
        runner.run.return_value = (0, '{', '')
        with pytest.raises(RuntimeError, match='not valid JSON'):
            instance._read_snapshot_manifest(
                runner, '/home/test/.sky_snapshots/test-cluster')

    def test_missing_rank_snapshot(self):
        runner = mock.MagicMock()
        runner.run.side_effect = [(0, '', ''), (1, '', '')]
        with pytest.raises(RuntimeError, match='rank 1'):
            instance._validate_snapshot_files(runner, _snapshot_manifest())

    def test_missing_job_db_snapshot(self):
        snapshot_dir = '/home/test/.sky_snapshots/test-cluster'
        manifest = _snapshot_manifest(snapshot_dir)
        generation_dir = instance._snapshot_generation_dir(
            snapshot_dir, manifest['generation'])
        manifest['job_db_path'] = instance._snapshot_job_db_path(generation_dir)
        runner = mock.MagicMock()
        runner.run.side_effect = [(0, '', ''), (0, '', ''), (1, '', '')]
        with pytest.raises(RuntimeError, match='missing job database'):
            instance._validate_snapshot_files(runner, manifest)


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


class TestStopInstances:
    """Tests Slurm container export and stop ordering."""

    @staticmethod
    def _setup(monkeypatch, nodes):
        client = mock.MagicMock()
        client.query_jobs.return_value = ['123']
        client.get_job_nodes.return_value = (nodes, [
            f'10.0.0.{i + 1}' for i in range(len(nodes))
        ])
        login_runner = mock.MagicMock()

        def run(command, **kwargs):
            del kwargs
            if command.startswith('cat '):
                return 0, 'ubuntu:24.04\n', ''
            return 0, '', ''

        login_runner.run.side_effect = run
        head_runner = mock.MagicMock()
        head_runner.run_driver.return_value = (0, '', '')
        monkeypatch.setattr(instance, '_make_slurm_client',
                            mock.MagicMock(return_value=client))
        monkeypatch.setattr(instance, '_make_login_node_runner',
                            mock.MagicMock(return_value=login_runner))
        monkeypatch.setattr(instance, '_resolve_sky_base_dir',
                            mock.MagicMock(return_value='/home/test'))
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

    def test_exports_all_nodes_then_publishes_manifest(self, monkeypatch):
        nodes = ['node-a', 'node-b']
        _, login_runner, head_runner, write_manifest, cancel_slurm_job = (
            self._setup(monkeypatch, nodes))

        instance.stop_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)

        head_runner.run_driver.assert_called_once()
        head_runner.run.assert_not_called()
        assert ('cancel_jobs_encoded_results'
                in head_runner.run_driver.call_args.args[0])
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
        assert manifest['num_nodes'] == 2
        assert manifest['job_db_path'] == (
            '/home/test/.sky_snapshots/test-cluster/generations/'
            f'{_NEW_SNAPSHOT_GENERATION}/jobs.db')
        assert [entry['node'] for entry in manifest['snapshots']] == nodes
        assert [entry['rank'] for entry in manifest['snapshots']] == [0, 1]
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
        assert shared_cleanup == (
            'rm -rf -- /home/test/.sky_clusters/test-cluster')

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

        instance.stop_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)
        cleanup = cancel_slurm_job.call_args.kwargs['pre_batch_cancel']
        cleanup()

        assert cleanup_slurm_allocation.call_args.kwargs == {
            'preserve_logs': True,
        }

    def test_empty_container_marker_requires_relaunch(self, monkeypatch):
        _, login_runner, head_runner, write_manifest, cancel_slurm_job = (
            self._setup(monkeypatch, ['node-a']))
        login_runner.run.side_effect = lambda command, **kwargs: (0, '', '')

        with pytest.raises(exceptions.NotSupportedError,
                           match='Relaunch the cluster before stopping it'):
            instance.stop_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)

        head_runner.run_driver.assert_not_called()
        write_manifest.assert_not_called()
        cancel_slurm_job.assert_not_called()

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
            instance.stop_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)

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

        instance.stop_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)

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
            instance.stop_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)

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
            instance.stop_instances(_CLUSTER, provider_config=_PROVIDER_CONFIG)

        commands = [call.args[0] for call in login_runner.run.call_args_list]
        assert not any(
            command.startswith(
                'rm -rf -- /home/test/.sky_snapshots/test-cluster')
            for command in commands)
        write_manifest.assert_not_called()
        cancel_slurm_job.assert_not_called()


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
        manifest = _snapshot_manifest('/home/test/.sky_snapshots/test-cluster',
                                      num_nodes=2)
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
