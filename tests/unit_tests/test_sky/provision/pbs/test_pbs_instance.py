"""Unit tests for sky.provision.pbs.instance."""
# pylint: disable=protected-access
import shlex
import subprocess
from unittest import mock

import pytest

from sky import exceptions
from sky.adaptors import pbs as pbs_adaptor
from sky.provision import common
from sky.provision.pbs import instance
from sky.provision.pbs import utils as pbs_utils
from sky.utils import command_runner
from sky.utils import status_lib

_CLUSTER = 'sky-test-cluster'
_PROVIDER_CONFIG = {
    'cluster': 'mycluster',
    'queue': 'workq',
    'provision_timeout': 120,
    'sky_base_dir': '/home/alice',
    'ssh_public_key_content': 'ssh-ed25519 AAAAC3 sky-key',
    'ssh': {
        'hostname': 'login.example.com',
        'port': '22',
        'user': 'alice',
        'private_key': '/keys/login_key',
    },
}
_QUEUE = pbs_adaptor.PbsQueue('workq', True, 'Execution', True, True,
                              '48:00:00', '01:00:00')


def _job(job_id='77.pbs',
         state='R',
         hosts=('node1', 'node2'),
         comment=None,
         run_count=1):
    return pbs_adaptor.JobInfo(job_id=job_id,
                               name=_CLUSTER,
                               state=state,
                               queue='workq',
                               exec_hosts=list(hosts),
                               comment=comment,
                               exit_status=None,
                               owner='alice@login',
                               run_count=run_count)


class TestQsubDirectives:

    def test_empty(self):
        assert instance._build_custom_qsub_directives({}) == []

    def test_values(self):
        lines = instance._build_custom_qsub_directives({
            'A': 'proj',
            'W': ['group_list=lab', 'umask=022'],
            'm': 'n',
            'V': True,
            'v': None,
            'nice': False,
            'priority': 10,
            'walltime': '04:00:00',
            'l': ['storage=gdata/x', 'select=5:ncpus=1', 'walltime=1:00:00'],
            '-P': 'p1',
        })
        assert lines == [
            '#PBS -A proj',
            '#PBS -P p1',
            '#PBS -V',
            '#PBS -W group_list=lab',
            '#PBS -W umask=022',
            '#PBS -l storage=gdata/x',
            '#PBS -l walltime=1:00:00',
            '#PBS -m n',
            '#PBS -priority 10',
            '#PBS -l walltime=04:00:00',
        ]

    def test_protected_dropped(self):
        lines = instance._build_custom_qsub_directives({
            'N': 'x',
            'q': 'other',
            'o': '/dev/null',
            'A': 'proj',
        })
        assert lines == ['#PBS -A proj']

    def test_newline_rejected(self):
        with pytest.raises(ValueError, match='Newline'):
            instance._build_custom_qsub_directives({'A': 'proj\nqdel'})

    def test_bad_walltime_rejected(self):
        with pytest.raises(ValueError, match='walltime'):
            instance._build_custom_qsub_directives({'walltime': '1 day'})
        with pytest.raises(ValueError, match='walltime'):
            instance._build_custom_qsub_directives({'l': 'walltime=abc'})

    def test_auto_walltime(self):
        assert instance._compute_walltime_directive(
            {}, _QUEUE, 'workq') == ('#PBS -l walltime=48:00:00')
        # User value wins, in either spelling.
        assert instance._compute_walltime_directive({'walltime': '1:00:00'},
                                                    _QUEUE, 'workq') is None
        assert instance._compute_walltime_directive({'l': ['walltime=1:00']},
                                                    _QUEUE, 'workq') is None
        no_max = _QUEUE._replace(max_walltime=None)
        assert instance._compute_walltime_directive({}, no_max, 'workq') is None
        assert instance._compute_walltime_directive({}, None, 'workq') is None

    def test_build_qsub_directives(self):
        text = instance._build_qsub_directives({'A': 'proj'}, _QUEUE, 'workq')
        assert text == '#PBS -l walltime=48:00:00\n#PBS -A proj'


class TestProvisionScript:

    def _script(self, **kwargs):
        args = dict(
            cluster_name_on_cloud=_CLUSTER,
            num_nodes=2,
            cpus=4,
            mem_in_mb=16384,
            gpu_resource='ngpus',
            accelerator_count=2,
            accelerator_type='A100',
            gpu_type_resource='gpu_type',
            directives='#PBS -A proj',
            sky_base_dir='/home/alice',
            sky_cluster_home_dir=f'/home/alice/.sky_clusters/{_CLUSTER}',
            ssh_public_key='ssh-ed25519 AAAA key')
        args.update(kwargs)
        return instance._build_provision_script(**args)

    def test_select_chunk(self):
        script = self._script()
        assert ('#PBS -l select=2:ncpus=4:mem=16384mb:ngpus=2:gpu_type=A100'
                in script)
        assert '#PBS -l place=scatter' in script
        assert '#PBS -r n' in script
        assert '#PBS -A proj' in script
        assert f'#PBS -o /home/alice/.sky_provision/{_CLUSTER}.spool' in script
        # Rank 0 runs in the batch script; other hosts go through pbsdsh -n.
        assert 'node_agent.sh 0 &' in script
        assert 'pbsdsh -n "$chunk" -- /bin/bash' in script
        assert 'rank="${1:-${PBS_NODENUM:-0}}"' in script
        assert "printf '%s\\n' 'ssh-ed25519 AAAA key'" in script

    def test_select_resources_and_place(self):
        script = self._script(select_resources={
            'tmp_size': '400gb',
            'mpiprocs': 4
        },
                              place='scatter:excl')
        assert ('#PBS -l select=2:ncpus=4:mem=16384mb:ngpus=2:gpu_type=A100'
                ':mpiprocs=4:tmp_size=400gb' in script)
        assert '#PBS -l place=scatter:excl' in script
        with pytest.raises(ValueError):
            self._script(select_resources={'a=b': 'c'})
        with pytest.raises(ValueError):
            self._script(place='excl\nqdel')

    def test_tmpdir(self):
        agent = instance._node_agent_script(_CLUSTER, '/home/a/c', None)
        assert 'tmp_base="${TMPDIR:-/tmp}"' in agent
        agent = instance._node_agent_script(_CLUSTER, '/home/a/c',
                                            '/scratch/$USER')
        assert 'tmp_base="/scratch/$USER"' in agent
        assert f'runtime_dir="$tmp_base/{_CLUSTER}"' in agent

    def test_select_chunk_without_gpu_type_or_memory(self):
        script = self._script(accelerator_type=None,
                              gpu_type_resource=None,
                              mem_in_mb=0)
        assert '#PBS -l select=2:ncpus=4:ngpus=2\n' in script
        script = self._script(accelerator_count=0, gpu_type_resource=None)
        assert '#PBS -l select=2:ncpus=4:mem=16384mb\n' in script

    def test_directives_precede_commands(self):
        script = self._script()
        lines = script.splitlines()
        first_command = next(i for i, line in enumerate(lines)
                             if line and not line.startswith('#'))
        assert all(
            line.startswith('#PBS')
            for line in lines[1:first_command]
            if line.startswith('#') and not line.startswith('# '))

    def test_scripts_are_valid_bash(self):
        for script in (self._script(),
                       instance._node_agent_script(_CLUSTER, '/home/a/c',
                                                   None)):
            result = subprocess.run(['bash', '-n'],
                                    input=script,
                                    text=True,
                                    capture_output=True,
                                    check=False)
            assert result.returncode == 0, result.stderr

    def test_node_agent_kills_cluster_processes_and_publishes_port(self):
        agent = instance._node_agent_script(_CLUSTER, '/home/a/c', None)
        assert 'SKY_RUNTIME_DIR=$runtime_dir' in agent
        assert '/home/a/c/.sky_sshd/rank${rank}.port' in agent
        assert '-o UsePAM=no' in agent
        assert '-o MaxStartups=100' in agent
        assert 'SetEnv=$session_env' in agent
        assert 'PBS_JOBID=$PBS_JOBID' in agent
        assert '-o AuthorizedKeysFile=/home/a/c/.sky_sshd/authorized_keys' in (
            agent)
        assert f'"$runtime_dir/.sky/{pbs_utils.PBS_MARKER_FILE}"' in agent
        assert ('printf \'%s %s %s\\n\' "$(hostname)" "$port" "$runtime_dir"'
                in agent)
        preferred = instance._preferred_sshd_port(_CLUSTER)
        assert instance._SSHD_PORT_RANGE_START <= preferred < (
            instance._SSHD_PORT_RANGE_START + instance._SSHD_PORT_RANGE_SIZE)
        assert f'port={preferred}' in agent


class TestQueryInstances:

    def test_states(self):
        client = mock.MagicMock()
        client.list_jobs_by_name.return_value = [
            _job('1.pbs', 'R', ('node1', 'node2')),
            _job('2.pbs', 'Q', (), comment='Not Running: no gpus'),
            _job('3.pbs', 'F', ()),
        ]
        with mock.patch.object(instance,
                               '_make_pbs_client',
                               return_value=client):
            statuses = instance.query_instances('c', _CLUSTER, _PROVIDER_CONFIG)
            assert statuses == {
                'job1.pbs-node1': (status_lib.ClusterStatus.UP, None),
                'job1.pbs-node2': (status_lib.ClusterStatus.UP, None),
                '2.pbs':
                    (status_lib.ClusterStatus.INIT, 'Not Running: no gpus'),
            }
            statuses = instance.query_instances('c',
                                                _CLUSTER,
                                                _PROVIDER_CONFIG,
                                                non_terminated_only=False)
            assert statuses['3.pbs'] == (None, None)
        client.list_jobs_by_name.assert_called_with(_CLUSTER,
                                                    include_finished=True)


class TestGetClusterInfo:

    def test_no_running_job(self):
        client = mock.MagicMock()
        client.list_jobs_by_name.return_value = [_job(state='Q', hosts=())]
        with mock.patch.object(instance,
                               '_make_pbs_client',
                               return_value=client):
            info = instance.get_cluster_info('c', _CLUSTER, _PROVIDER_CONFIG)
        assert info.head_instance_id is None
        assert info.provider_name == 'pbs'

    def test_running_job(self):
        client = mock.MagicMock()
        client.list_jobs_by_name.return_value = [_job()]
        runner = mock.MagicMock()
        runner.run.return_value = (0, 'rank0.port node1 23456 /tmp/j/x\n'
                                   'rank1.port node2 23457 /tmp/j/x\n', '')
        with mock.patch.object(instance, '_make_pbs_client',
                               return_value=client), \
             mock.patch.object(instance, '_make_login_node_runner',
                               return_value=runner):
            info = instance.get_cluster_info('c', _CLUSTER, _PROVIDER_CONFIG)
        assert info.head_instance_id == 'job77.pbs-node1'
        head = info.get_head_instance()
        assert head.internal_ip == 'node1'
        assert head.external_ip == 'login.example.com'
        assert head.ssh_port == 23456
        assert head.tags['node'] == 'node1'
        assert head.tags['job_id'] == '77.pbs'
        assert head.tags['runtime_dir'] == '/tmp/j/x'
        worker = info.instances['job77.pbs-node2'][0]
        assert worker.ssh_port == 23457

    def test_missing_port_file(self):
        client = mock.MagicMock()
        client.list_jobs_by_name.return_value = [_job()]
        runner = mock.MagicMock()
        runner.run.return_value = (0, 'rank0.port node1 23456 /tmp/x\n', '')
        with mock.patch.object(instance, '_make_pbs_client',
                               return_value=client), \
             mock.patch.object(instance, '_make_login_node_runner',
                               return_value=runner):
            with pytest.raises(RuntimeError, match=r'node\(s\) \[1\]'):
                instance.get_cluster_info('c', _CLUSTER, _PROVIDER_CONFIG)


class TestGetCommandRunners:

    def test_runners(self, tmp_path):
        client = mock.MagicMock()
        client.list_jobs_by_name.return_value = [_job()]
        login_runner = mock.MagicMock()
        login_runner.run.return_value = (0, 'rank0.port node1 23456 /tmp/j/x\n'
                                         'rank1.port node2 23457 /tmp/j/y\n',
                                         '')
        key = tmp_path / 'sky-key'
        key.write_text('')
        with mock.patch.object(instance, '_make_pbs_client',
                               return_value=client), \
             mock.patch.object(instance, '_make_login_node_runner',
                               return_value=login_runner):
            info = instance.get_cluster_info('c', _CLUSTER, _PROVIDER_CONFIG)
            config = dict(_PROVIDER_CONFIG)
            config['ssh'] = dict(config['ssh'], private_key=str(key))
            info.provider_config = config
            runners = instance.get_command_runners(info,
                                                   ssh_private_key=str(key),
                                                   ssh_user='alice')
        assert len(runners) == 2
        head = runners[0]
        assert isinstance(head, command_runner.PbsCommandRunner)
        assert head.node == ('node1', 23456)
        assert head.ssh_user == 'alice'
        assert head.ssh_private_key == str(key)
        assert head.sky_dir == f'/home/alice/.sky_clusters/{_CLUSTER}'
        assert head.skypilot_runtime_dir == '/tmp/j/x'
        assert runners[1].skypilot_runtime_dir == '/tmp/j/y'
        cmd = ' '.join(
            head.ssh_base_command(
                ssh_mode=command_runner.SshMode.NON_INTERACTIVE,
                port_forward=None,
                connect_timeout=None))
        assert 'alice@login.example.com' in cmd
        assert cmd.endswith('alice@node1')
        assert '-o Port=23456' in cmd


class TestTerminate:

    def test_terminate_deletes_and_cleans(self):
        client = mock.MagicMock()
        calls = []

        def _list(name, include_finished=False):
            del name, include_finished
            calls.append(None)
            if len(calls) == 1:
                return [_job()]
            return []

        client.list_jobs_by_name.side_effect = _list
        client.get_jobs_state_by_name.return_value = ['F']
        runner = mock.MagicMock()
        runner.run.return_value = (0, '', '')
        with mock.patch.object(instance, '_make_pbs_client',
                               return_value=client), \
             mock.patch.object(instance, '_make_login_node_runner',
                               return_value=runner), \
             mock.patch.object(instance.pbs_utils, 'is_inside_pbs_cluster',
                               return_value=False):
            instance.terminate_instances(_CLUSTER, _PROVIDER_CONFIG)
        client.delete_job.assert_called_once_with('77.pbs')
        cleanup_cmd = runner.run.call_args.args[0]
        assert f'/home/alice/.sky_clusters/{_CLUSTER}' in cleanup_cmd

    def test_terminate_escalates_to_force(self):
        client = mock.MagicMock()
        client.list_jobs_by_name.return_value = [_job()]
        states = iter([['R'], ['F']])
        client.get_jobs_state_by_name.side_effect = lambda _: next(states)
        runner = mock.MagicMock()
        runner.run.return_value = (0, '', '')
        with mock.patch.object(instance, '_make_pbs_client',
                               return_value=client), \
             mock.patch.object(instance, '_make_login_node_runner',
                               return_value=runner), \
             mock.patch.object(instance.pbs_utils, 'is_inside_pbs_cluster',
                               return_value=False), \
             mock.patch.object(instance, '_JOB_TERMINATION_TIMEOUT_SECONDS', 0), \
             mock.patch.object(instance.time, 'sleep'):
            instance.terminate_instances(_CLUSTER, _PROVIDER_CONFIG)
        assert client.delete_job.call_args_list == [
            mock.call('77.pbs'),
            mock.call('77.pbs', force=True),
        ]

    def test_terminate_inside_cluster_only_deletes(self):
        client = mock.MagicMock()
        client.list_jobs_by_name.return_value = [_job()]
        with mock.patch.object(instance.pbs_adaptor_for_tests
                               if hasattr(instance, 'pbs_adaptor_for_tests')
                               else instance.pbs, 'PbsClient',
                               return_value=client) as client_cls, \
             mock.patch.object(instance.pbs_utils, 'is_inside_pbs_cluster',
                               return_value=True):
            instance.terminate_instances(_CLUSTER, _PROVIDER_CONFIG)
        client_cls.assert_called_once_with(is_inside_pbs_cluster=True)
        client.delete_job.assert_called_once_with('77.pbs')
        client.get_jobs_state_by_name.assert_not_called()

    def test_stop_not_supported(self):
        with pytest.raises(exceptions.NotSupportedError):
            instance.stop_instances(_CLUSTER, _PROVIDER_CONFIG)


class TestWaitForJobNodes:

    def test_reports_pending_then_returns(self):
        client = mock.MagicMock()
        jobs = iter([
            _job(state='Q', hosts=(), comment='Not Running: busy'),
            _job(state='R'),
        ])
        client.get_job.side_effect = lambda _: next(jobs)
        client.get_pending_job_count.return_value = 3
        on_pending = mock.MagicMock()
        with mock.patch.object(instance.time, 'sleep'):
            job = instance._wait_for_job_nodes(client, '77.pbs', 60, 'workq',
                                               on_pending)
        assert job.exec_hosts == ['node1', 'node2']
        on_pending.assert_called_once_with('Q', 'Not Running: busy', 3)

    def test_terminal_state_raises(self):
        client = mock.MagicMock()
        client.get_job.return_value = _job(state='F', hosts=())
        with pytest.raises(RuntimeError, match='terminated'):
            instance._wait_for_job_nodes(client, '77.pbs', 60, 'workq',
                                         mock.MagicMock())

    def test_timeout(self):
        client = mock.MagicMock()
        client.get_job.return_value = _job(state='Q', hosts=())
        with mock.patch.object(instance.time, 'sleep'):
            with pytest.raises(TimeoutError):
                instance._wait_for_job_nodes(client, '77.pbs', 0, 'workq',
                                             mock.MagicMock())


class TestCreateVirtualInstance:

    def _config(self, num_nodes=2):
        return common.ProvisionConfig(
            provider_config=dict(_PROVIDER_CONFIG),
            authentication_config={
                'ssh_user': 'alice',
                'ssh_private_key': '/keys/sky-key',
            },
            docker_config={},
            node_config={
                'instance_type': '4CPU--16GB--A100:2',
                'cpus': '4',
                'memory': '16',
                'accelerator_type': 'A100',
                'accelerator_count': '2',
                'gpu_resource': 'ngpus',
                'gpu_type_resource': 'gpu_type',
                'qsub_options': {
                    'A': 'proj'
                },
            },
            count=num_nodes,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

    def test_submits_and_waits(self, tmp_path):
        client = mock.MagicMock()
        client.query_jobs.return_value = []
        client.submit_job.return_value = '77.pbs'
        client.get_job.return_value = _job()
        client.get_job_state.return_value = 'R'
        login_runner = mock.MagicMock()
        port_listing = ('rank0.port node1 23456 /tmp/x\n'
                        'rank1.port node2 23457 /tmp/x\n')

        def _run(cmd, **kwargs):
            del kwargs
            text = cmd if isinstance(cmd, str) else shlex.join(cmd)
            if 'rank*.port' in text:
                return (0, port_listing, '')
            return (0, '', '')

        login_runner.run.side_effect = _run
        uploaded = {}

        def _rsync(src, tgt, **kwargs):
            del kwargs
            with open(src, 'r', encoding='utf-8') as f:
                uploaded[tgt] = f.read()

        login_runner.rsync.side_effect = _rsync
        node_runner = mock.MagicMock()
        node_runner.run.return_value = (0, '', '')
        with mock.patch.object(instance, '_make_pbs_client',
                               return_value=client), \
             mock.patch.object(instance, '_make_login_node_runner',
                               return_value=login_runner), \
             mock.patch.object(instance, 'get_command_runners',
                               return_value=[node_runner, node_runner]), \
             mock.patch.object(instance.pbs_utils, 'get_queue_info',
                               return_value=_QUEUE), \
             mock.patch.object(instance.global_user_state,
                               'add_cluster_event'), \
             mock.patch.object(instance.time, 'sleep'):
            record = instance.run_instances('mycluster', 'test-cluster',
                                            _CLUSTER, self._config())
        assert record.provider_name == 'pbs'
        assert record.region == 'mycluster'
        assert record.zone == 'workq'
        assert record.head_instance_id == 'job77.pbs-node1'
        assert record.created_instance_ids == [
            'job77.pbs-node1', 'job77.pbs-node2'
        ]
        client.submit_job.assert_called_once_with(
            'workq', _CLUSTER, f'/home/alice/.sky_provision/{_CLUSTER}.sh',
            '/home/alice/.sky_provision')
        script = uploaded[f'/home/alice/.sky_provision/{_CLUSTER}.sh']
        assert ('#PBS -l select=2:ncpus=4:mem=16384mb:ngpus=2:gpu_type=A100'
                in script)
        assert '#PBS -l walltime=48:00:00' in script
        assert '#PBS -A proj' in script
        assert 'ssh-ed25519 AAAAC3 sky-key' in script
        # Every node's sshd was reached before provisioning returned.
        assert node_runner.run.call_count == 2

    def test_uses_nodes_of_rerun_job(self):
        # The job first runs on node9, fails to start there and is rerun on
        # node1 and node2, where the node agents come up.
        client = mock.MagicMock()
        client.query_jobs.return_value = []
        client.submit_job.return_value = '77.pbs'
        jobs = iter([_job(hosts=('node9', 'node2'))])
        client.get_job.side_effect = lambda _: next(jobs, _job())
        client.get_job_state.return_value = 'R'
        login_runner = mock.MagicMock()
        login_runner.run.return_value = (
            0, 'rank0.port node1 23456 /tmp/x\nrank1.port node2 23457 /tmp/x\n',
            '')
        node_runner = mock.MagicMock()
        node_runner.run.return_value = (0, '', '')
        with mock.patch.object(instance, '_make_pbs_client',
                               return_value=client), \
             mock.patch.object(instance, '_make_login_node_runner',
                               return_value=login_runner), \
             mock.patch.object(instance, 'get_command_runners',
                               return_value=[node_runner, node_runner]
                              ) as get_runners, \
             mock.patch.object(instance.pbs_utils, 'get_queue_info',
                               return_value=_QUEUE), \
             mock.patch.object(instance.global_user_state,
                               'add_cluster_event'), \
             mock.patch.object(instance.time, 'sleep'):
            record = instance.run_instances('mycluster', 'test-cluster',
                                            _CLUSTER, self._config())
        cluster_info = get_runners.call_args.args[0]
        assert sorted(inst.internal_ip
                      for insts in cluster_info.instances.values()
                      for inst in insts) == ['node1', 'node2']
        assert record.head_instance_id == 'job77.pbs-node1'
        assert record.created_instance_ids == [
            'job77.pbs-node1', 'job77.pbs-node2'
        ]

    def test_reuses_existing_job(self):
        client = mock.MagicMock()
        client.query_jobs.return_value = ['77.pbs']
        client.get_job.return_value = _job()
        client.get_job_state.return_value = 'R'
        login_runner = mock.MagicMock()
        login_runner.run.return_value = (
            0, 'rank0.port node1 23456 /tmp/x\nrank1.port node2 23457 /tmp/x\n',
            '')
        node_runner = mock.MagicMock()
        node_runner.run.return_value = (0, '', '')
        with mock.patch.object(instance, '_make_pbs_client',
                               return_value=client), \
             mock.patch.object(instance, '_make_login_node_runner',
                               return_value=login_runner), \
             mock.patch.object(instance, 'get_command_runners',
                               return_value=[node_runner, node_runner]), \
             mock.patch.object(instance.global_user_state,
                               'add_cluster_event'), \
             mock.patch.object(instance.time, 'sleep'):
            record = instance.run_instances('mycluster', 'test-cluster',
                                            _CLUSTER, self._config())
        client.submit_job.assert_not_called()
        assert record.created_instance_ids == []
        assert record.head_instance_id == 'job77.pbs-node1'

    @pytest.mark.parametrize('failed_job,error', [
        (_job(state='F', hosts=()), 'exited'),
        (_job(state='H',
              hosts=(),
              comment='job held, too many failed attempts to run'),
         'held .*too many failed attempts'),
        (_job(hosts=('node9',),
              comment='Job run at Wed on (node9:ncpus=4)',
              run_count=5), r'failed to start .* 5 times \(last on node9\)'),
    ])
    def test_job_fails_before_ready_shows_log(self, failed_job, error):
        # The job runs, then fails while the node agents are starting.
        client = mock.MagicMock()
        client.query_jobs.return_value = []
        client.submit_job.return_value = '77.pbs'
        jobs = iter([_job()])
        client.get_job.side_effect = lambda _: next(jobs, failed_job)
        login_runner = mock.MagicMock()

        def _run(cmd, **kwargs):
            del kwargs
            text = cmd if isinstance(cmd, str) else shlex.join(cmd)
            if 'rank*.port' in text:
                return (0, '', '')
            if text.startswith('cat'):
                return (0, '[node-agent] ERROR: no sshd binary found', '')
            return (0, '', '')

        login_runner.run.side_effect = _run
        with mock.patch.object(instance, '_make_pbs_client',
                               return_value=client), \
             mock.patch.object(instance, '_make_login_node_runner',
                               return_value=login_runner), \
             mock.patch.object(instance.pbs_utils, 'get_queue_info',
                               return_value=_QUEUE), \
             mock.patch.object(instance.global_user_state,
                               'add_cluster_event'), \
             mock.patch.object(instance.time, 'sleep'), \
             mock.patch.object(instance.logger, 'error') as log_error:
            with pytest.raises(RuntimeError, match=error):
                instance.run_instances('mycluster', 'test-cluster', _CLUSTER,
                                       self._config())
        assert 'no sshd binary' in log_error.call_args.args[0]
