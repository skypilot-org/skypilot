"""Tests for the PBS adaptor."""
# pylint: disable=protected-access
from unittest import mock

import pytest

from sky import exceptions
from sky.adaptors import pbs

_PBSNODES_OUTPUT = """\
node1
     Mom = node1.cluster.local
     ntype = PBS
     state = free
     pcpus = 32
     resources_available.arch = linux
     resources_available.host = node1
     resources_available.Qlist = gpuq,workq
     resources_available.mem = 263782400kb
     resources_available.ncpus = 32
     resources_available.ngpus = 4
     resources_available.gpu_type = A100
     resources_available.vnode = node1
     resources_assigned.accelerator_memory = 0kb
     resources_assigned.mem = 0kb
     resources_assigned.ncpus = 0
     resources_assigned.ngpus = 0
     resv_enable = True
     sharing = default_shared

node2
     Mom = node2.cluster.local
     ntype = PBS
     state = job-busy
     pcpus = 64
     jobs = 101.pbs/0, 101.pbs/1, 102.pbs/0
     resources_available.host = node2
     resources_available.mem = 128gb
     resources_available.ncpus = 64
     resources_available.ngpus = 8
     resources_available.gpu_type = H100
     resources_available.vnode = node2
     resources_assigned.mem = 65536mb
     resources_assigned.ncpus = 64
     resources_assigned.ngpus = 8
     queue = gpuq
     sharing = default_shared

cpu1
     Mom = cpu1
     state = offline,down
     resources_available.mem = 32gb
     resources_available.ncpus = 16
     resources_assigned.ncpus = 0

"""

_QSTAT_Q_OUTPUT = """\
Queue: workq
    queue_type = Execution
    total_jobs = 3
    state_count = Transit:0 Queued:2 Held:0 Waiting:0 Running:1 Exiting:0 Begun:0
    resources_max.walltime = 24:00:00
    resources_default.walltime = 01:00:00
    default_chunk.Qlist = workq
    enabled = True
    started = True

Queue: gpuq
    queue_type = Execution
    total_jobs = 0
    from_route_only = True
    enabled = True
    started = False

Queue: route
    queue_type = Route
    route_destinations = workq
    enabled = True
    started = True

"""

_QSTAT_B_OUTPUT = """\
Server: pbs
    server_state = Active
    server_host = pbs.cluster.local
    scheduling = True
    default_queue = workq
    pbs_version = 2022.1.1

"""

_QSTAT_F_OUTPUT = """\
Job Id: 123.pbs
    Job_Name = sky-abc-user
    Job_Owner = alice@login
    job_state = R
    queue = workq
    server = pbs
    exec_host = node1/0*4+node2/0*4+node2/4*4
    exec_vnode = (node1:ncpus=4)+(node2:ncpus=8)
    Resource_List.ncpus = 12
    Resource_List.nodect = 2
    Resource_List.place = scatter
    Resource_List.select = 2:ncpus=4
    Variable_List = PBS_O_HOME=/home/alice,PBS_O_LANG=en_US.UTF-8,PBS_O_PATH=/
\tusr/local/bin:/usr/bin,PBS_O_WORKDIR=/home/alice
    comment = Job run at Mon Oct 26 at 10:00 on (node1:ncpus=4)+(node2:ncpus=8)
    project = _pbs_project_default

Job Id: 124.pbs
    Job_Name = sky-abc-user
    Job_Owner = alice@login
    job_state = Q
    queue = workq
    comment = Not Running: Insufficient amount of resource: ngpus (R: 1 A: 0 T: 4)

Job Id: 125.pbs
    Job_Name = other
    job_state = F
    queue = workq
    Exit_status = 271

"""


class TestParsers:

    @pytest.mark.parametrize('value,expected', [
        ('128gb', 128.0),
        ('1024mb', 1.0),
        ('263782400kb', 263782400 / 1024 / 1024),
        ('1tb', 1024.0),
        ('2097152b', 2097152 / 1024**3),
        ('4GB', 4.0),
        (None, 0.0),
    ])
    def test_parse_size_to_gb(self, value, expected):
        assert pbs.parse_size_to_gb(value) == pytest.approx(expected)

    def test_parse_size_rejects_garbage(self):
        with pytest.raises(ValueError):
            pbs.parse_size_to_gb('lots')

    @pytest.mark.parametrize('exec_host,expected', [
        ('node1/0*8+node2/0*8', ['node1', 'node2']),
        ('node1/0*4+node1/4*4+node2/0', ['node1', 'node2']),
        ('node1/0', ['node1']),
        ('', []),
        (None, []),
    ])
    def test_parse_exec_host(self, exec_host, expected):
        assert pbs.parse_exec_host(exec_host) == expected

    def test_parse_attribute_blocks_joins_continuations(self):
        blocks = pbs.parse_attribute_blocks(_QSTAT_F_OUTPUT)
        assert [header for header, _ in blocks
               ] == ['Job Id: 123.pbs', 'Job Id: 124.pbs', 'Job Id: 125.pbs']
        attrs = blocks[0][1]
        assert attrs['Variable_List'] == (
            'PBS_O_HOME=/home/alice,PBS_O_LANG=en_US.UTF-8,PBS_O_PATH=/'
            'usr/local/bin:/usr/bin,PBS_O_WORKDIR=/home/alice')
        # A value may itself contain ' = '.
        assert attrs['comment'].startswith('Job run at')

    def test_parse_nodes_output(self):
        nodes = pbs.parse_nodes_output(_PBSNODES_OUTPUT, 'ngpus',
                                       ('gpu_type', 'gputype'))
        assert [n.node for n in nodes] == ['node1', 'node2', 'cpu1']
        node1, node2, cpu1 = nodes
        assert node1.cpus == 32
        assert node1.memory_gb == pytest.approx(263782400 / 1024 / 1024)
        assert node1.gpus == 4
        assert node1.gpu_type == 'A100'
        assert node1.gpu_type_resource == 'gpu_type'
        assert node1.queue is None
        assert node1.jobs == []
        assert node1.qlist == 'gpuq,workq'
        assert cpu1.qlist is None
        assert node2.state == 'job-busy'
        assert node2.queue == 'gpuq'
        assert node2.assigned_cpus == 64
        assert node2.assigned_gpus == 8
        assert node2.jobs == ['101.pbs', '102.pbs']
        assert cpu1.gpus == 0
        assert cpu1.gpu_type is None
        assert not pbs.is_node_available(cpu1.state)
        assert pbs.is_node_available(node2.state)

    def test_parse_nodes_output_custom_gpu_resource(self):
        output = ('n\n     state = free\n     resources_available.ncpus = 8\n'
                  '     resources_available.gpus = 2\n'
                  '     resources_available.gputype = v100\n')
        node = pbs.parse_nodes_output(output, 'gpus', ('gputype',))[0]
        assert node.gpus == 2
        assert node.gpu_type == 'v100'
        assert node.gpu_type_resource == 'gputype'

    def test_parse_queues_output(self):
        queues = pbs.parse_queues_output(_QSTAT_Q_OUTPUT, 'workq')
        assert [q.name for q in queues] == ['workq', 'gpuq', 'route']
        workq, gpuq, route = queues
        assert workq.is_default
        assert workq.max_walltime == '24:00:00'
        assert workq.default_walltime == '01:00:00'
        assert workq.enabled and workq.started
        assert not gpuq.started
        assert gpuq.max_walltime is None
        assert gpuq.from_route_only
        assert not workq.from_route_only
        assert workq.qlist == 'workq'
        assert gpuq.qlist is None
        assert route.queue_type == 'Route'
        assert route.route_destinations == ('workq',)
        assert workq.route_destinations == ()

    def test_parse_jobs_output(self):
        jobs = pbs.parse_jobs_output(_QSTAT_F_OUTPUT)
        assert len(jobs) == 3
        running, queued, finished = jobs
        assert running.job_id == '123.pbs'
        assert running.name == 'sky-abc-user'
        assert running.state == 'R'
        assert running.exec_hosts == ['node1', 'node2']
        assert running.owner == 'alice@login'
        assert queued.state == 'Q'
        assert queued.exec_hosts == []
        assert queued.comment.startswith('Not Running')
        assert finished.state == 'F'
        assert finished.exit_status == 271


def _client_with_runner(outputs):
    """A PbsClient whose runner replays canned (rc, stdout, stderr) triples.

    ``outputs`` maps a substring of the command to the triple to return.
    """
    client = pbs.PbsClient(is_inside_pbs_cluster=True)
    runner = mock.MagicMock()
    calls = []

    def _run(cmd, **kwargs):
        del kwargs
        calls.append(cmd)
        for needle, result in outputs.items():
            if needle in cmd:
                return result
        raise AssertionError(f'Unexpected command: {cmd}')

    runner.run.side_effect = _run
    client._runner = runner
    return client, calls


class TestPbsClient:

    def test_commands_carry_pbs_path(self):
        client, calls = _client_with_runner({'qstat -B': (0, 'Server\n', '')})
        client.info()
        assert calls[0].startswith('export PATH="$PATH:/opt/pbs/bin"; ')

    def test_get_queues_uses_default_queue_first(self):
        client, _ = _client_with_runner({
            'qstat -B -f': (0, (f'\n{pbs._BATCH_BEGIN}\n{_QSTAT_B_OUTPUT}\n'
                                f'{pbs._BATCH_END} 0\n'
                                f'\n{pbs._BATCH_BEGIN}\n{_QSTAT_Q_OUTPUT}\n'
                                f'{pbs._BATCH_END} 0\n'), '')
        })
        # Execution queues only, enabled or not (the utils layer filters
        # on enabled/started).
        assert client.get_queues() == ['workq', 'gpuq']

    def test_batched_commands_skip_login_banner(self):
        client, _ = _client_with_runner({
            'pbsnodes -a': (0, (f'Welcome to the cluster!\n\n{pbs._BATCH_BEGIN}'
                                f'\n{_PBSNODES_OUTPUT}\n{pbs._BATCH_END} 0\n'
                                f'\n{pbs._BATCH_BEGIN}\n{_QSTAT_B_OUTPUT}\n'
                                f'{pbs._BATCH_END} 0\n'
                                f'\n{pbs._BATCH_BEGIN}\n{_QSTAT_Q_OUTPUT}\n'
                                f'{pbs._BATCH_END} 0\n'), '')
        })
        snapshot = client.get_inventory_snapshot('ngpus', ('gpu_type',))
        assert [n.node for n in snapshot.nodes] == ['node1', 'node2', 'cpu1']
        assert snapshot.queues is not None
        assert next(q for q in snapshot.queues if q.is_default).name == 'workq'

    def test_run_command_returns_framed_output(self):
        client, _ = _client_with_runner({
            'echo hi': (0, (f'banner\n\n{pbs._BATCH_BEGIN}\nhi\n'
                            f'{pbs._BATCH_END} 3\n'), 'err')
        })
        assert client.run_command('echo hi') == (3, 'hi', 'err')

    def test_info_nodes_without_nodes(self):
        client, _ = _client_with_runner(
            {'pbsnodes -a': (1, '', 'pbsnodes: Server has no node list')})
        assert client.info_nodes('ngpus', ('gpu_type',)) == []

    def test_list_jobs_by_name_filters_and_selects_user(self):
        client, calls = _client_with_runner(
            {'qselect': (0, _QSTAT_F_OUTPUT, '')})
        jobs = client.list_jobs_by_name('sky-abc-user')
        assert [j.job_id for j in jobs] == ['123.pbs', '124.pbs']
        assert '-u "$(id -un)"' in calls[0]
        assert '-x' not in calls[0]
        assert client.query_jobs('sky-abc-user', ['Q']) == ['124.pbs']
        assert client.query_jobs('sky-abc-user',
                                 ['R', 'Q']) == ['123.pbs', '124.pbs']

    def test_query_jobs_includes_history_for_terminal_states(self):
        client, calls = _client_with_runner(
            {'qselect': (0, _QSTAT_F_OUTPUT, '')})
        client.query_jobs('other', ['F'])
        assert 'qselect -N other -u "$(id -un)" -x' in calls[0]
        assert 'qstat -x -f' in calls[0]

    def test_list_jobs_tolerates_job_vanishing(self):
        client, _ = _client_with_runner({
            'qselect': (153, _QSTAT_F_OUTPUT, 'qstat: Unknown Job Id 126.pbs')
        })
        assert len(client.list_jobs_by_name('sky-abc-user')) == 2

    def test_list_jobs_raises_on_other_errors(self):
        client, _ = _client_with_runner(
            {'qselect': (1, '', 'qselect: cannot connect to server')})
        with pytest.raises(exceptions.CommandError):
            client.list_jobs_by_name('sky-abc-user')

    def test_get_job_not_found(self):
        client, _ = _client_with_runner(
            {'qstat -x -f 999.pbs': (153, '', 'qstat: Unknown Job Id 999.pbs')})
        assert client.get_job('999.pbs') is None
        assert client.get_job_state('999.pbs') is None

    def test_get_job_nodes(self):
        client, _ = _client_with_runner({
            'qstat -x -f 123.pbs': (0, _QSTAT_F_OUTPUT, ''),
        })
        assert client.get_job_nodes('123.pbs') == ['node1', 'node2']
        client, _ = _client_with_runner({
            'qstat -x -f 124.pbs': (0, _QSTAT_F_OUTPUT.split('Job Id: 124')[0] +
                                    'Job Id: 124.pbs\n    job_state = Q\n', ''),
        })
        with pytest.raises(RuntimeError, match='not be running'):
            client.get_job_nodes('124.pbs')

    def test_submit_job(self):
        client, calls = _client_with_runner(
            {'qsub': (0, 'Loading modules...\n4567.pbs\n', '')})
        job_id = client.submit_job('workq', 'sky-abc', '/home/u/s.sh',
                                   '/home/u')
        assert job_id == '4567.pbs'
        assert "cd /home/u && qsub -q workq -N sky-abc -r n /home/u/s.sh" in (
            calls[0])

    def test_submit_job_bad_output(self):
        client, _ = _client_with_runner(
            {'qsub': (0, 'qsub: something odd happened', '')})
        with pytest.raises(RuntimeError, match='job id'):
            client.submit_job('workq', 'sky-abc', '/s.sh', '/home/u')

    def test_delete_job_tolerates_gone(self):
        client, calls = _client_with_runner(
            {'qdel': (153, '', 'qdel: Job has finished 123.pbs')})
        client.delete_job('123.pbs')
        client.delete_job('123.pbs', force=True)
        assert calls[1].endswith('qdel -W force 123.pbs')

    def test_cancel_jobs_by_name_skips_finished(self):
        client, calls = _client_with_runner({
            'qselect': (0, _QSTAT_F_OUTPUT, ''),
            'qdel': (0, '', ''),
        })
        client.cancel_jobs_by_name('sky-abc-user')
        deleted = [c for c in calls if 'qdel' in c]
        assert len(deleted) == 2
        assert all('125.pbs' not in c for c in deleted)

    def test_get_pending_job_count(self):
        client, _ = _client_with_runner(
            {'qselect -q workq -s Q': (0, '1.pbs\n2.pbs\n3.pbs\n', '')})
        assert client.get_pending_job_count('workq',
                                            exclude_job_id='2.pbs') == 2
        client, _ = _client_with_runner({'qselect -q workq': (1, '', 'err')})
        assert client.get_pending_job_count('workq') == -1

    def test_get_env(self):
        client, _ = _client_with_runner(
            {'env': (0, 'HOME=/home/u\nX=1=2\n', '')})
        assert client.get_env() == {'HOME': '/home/u', 'X': '1=2'}

    def test_check_dir_shared_fs(self):
        client, _ = _client_with_runner({'stat -f': (0, 'NFS\n', '')})
        assert client.check_dir_shared_fs('/home/u') == 'nfs'
