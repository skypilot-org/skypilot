"""Unit tests for Slurm adaptor."""

import unittest.mock as mock

import pytest

from sky.adaptors import slurm


class TestGetPartitions:
    """Test SlurmClient.get_partitions()."""

    def test_get_partitions_parses_multiple_partitions(self):
        """Test parsing multiple partitions from scontrol output."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        mock_output = """PartitionName=dev AllowGroups=ALL AllowAccounts=ALL AllowQos=ALL AllocNodes=ALL Default=YES QoS=N/A DefaultTime=NONE DisableRootJobs=NO ExclusiveUser=NO ExclusiveTopo=NO GraceTime=0 Hidden=NO MaxNodes=UNLIMITED MaxTime=UNLIMITED MinNodes=0 LLN=NO MaxCPUsPerNode=UNLIMITED MaxCPUsPerSocket=UNLIMITED NodeSets=ALL Nodes=ip-10-3-0-193,ip-10-3-68-50,ip-10-3-200-46,ip-10-3-201-35,ip-10-3-215-227,ip-10-3-225-110 PriorityJobFactor=1 PriorityTier=1 RootOnly=NO ReqResv=NO OverSubscribe=NO OverTimeLimit=NONE PreemptMode=OFF State=UP TotalCPUs=248 TotalNodes=6 SelectTypeParameters=NONE JobDefaults=(null) DefMemPerNode=UNLIMITED MaxMemPerNode=UNLIMITED TRES=cpu=248,mem=1216G,node=6,billing=248,gres/gpu=12
PartitionName=CPU nodes (amd) AllowGroups=ALL AllowAccounts=ALL AllowQos=ALL AllocNodes=ALL Default=NO QoS=N/A DefaultTime=NONE DisableRootJobs=NO ExclusiveUser=NO ExclusiveTopo=NO GraceTime=0 Hidden=NO MaxNodes=UNLIMITED MaxTime=UNLIMITED MinNodes=0 LLN=NO MaxCPUsPerNode=UNLIMITED MaxCPUsPerSocket=UNLIMITED Nodes=ip-10-3-0-193,ip-10-3-215-227 PriorityJobFactor=1 PriorityTier=1 RootOnly=NO ReqResv=NO OverSubscribe=NO OverTimeLimit=NONE PreemptMode=OFF State=UP TotalCPUs=4 TotalNodes=2 SelectTypeParameters=NONE JobDefaults=(null) DefMemPerNode=UNLIMITED MaxMemPerNode=UNLIMITED TRES=cpu=4,mem=32G,node=2,billing=4
PartitionName=GPU nodes (nvidia) AllowGroups=ALL AllowAccounts=ALL AllowQos=ALL AllocNodes=ALL Default=NO QoS=N/A DefaultTime=NONE DisableRootJobs=NO ExclusiveUser=NO ExclusiveTopo=NO GraceTime=0 Hidden=NO MaxNodes=UNLIMITED MaxTime=UNLIMITED MinNodes=0 LLN=NO MaxCPUsPerNode=UNLIMITED MaxCPUsPerSocket=UNLIMITED Nodes=ip-10-3-68-50,ip-10-3-200-46 PriorityJobFactor=1 PriorityTier=1 RootOnly=NO ReqResv=NO OverSubscribe=NO OverTimeLimit=NONE PreemptMode=OFF State=UP TotalCPUs=240 TotalNodes=2 SelectTypeParameters=NONE JobDefaults=(null) DefMemPerNode=UNLIMITED MaxMemPerNode=UNLIMITED TRES=cpu=240,mem=1152G,node=2,billing=240,gres/gpu=12"""
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, mock_output, '')

            result = client.get_partitions()
            mock_run.assert_called_once_with(
                'scontrol show partitions -o',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )

            assert result == ['dev', 'CPU nodes (amd)', 'GPU nodes (nvidia)']


class TestInfoNodes:
    """Test SlurmClient.info_nodes()."""

    def test_info_nodes_multiple_nodes(self):
        """Test parsing multiple nodes with different configurations."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        mock_output = (
            f'node1{slurm.SEP}idle{slurm.SEP}(null){slurm.SEP}2{slurm.SEP}16384{slurm.SEP}dev\n'
            f'node2{slurm.SEP}mix{slurm.SEP}gpu:a10g:8{slurm.SEP}192{slurm.SEP}786432{slurm.SEP}gpu nodes (RESERVED)\n'
            f'node3{slurm.SEP}alloc{slurm.SEP}(null){slurm.SEP}4{slurm.SEP}32768{slurm.SEP}tpu nodes'
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, mock_output, '')

            result = client.info_nodes()
            mock_run.assert_called_once_with(
                f'sinfo -h --Node -o "%N{slurm.SEP}%t{slurm.SEP}%G{slurm.SEP}%c{slurm.SEP}%m{slurm.SEP}%P"',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )

            assert len(result) == 3
            assert result[0].node == 'node1'
            assert result[0].state == 'idle'
            assert result[0].gres == '(null)'
            assert result[0].cpus == 2
            assert result[0].memory_gb == 16
            assert result[0].partition == 'dev'

            assert result[1].node == 'node2'
            assert result[1].state == 'mix'
            assert result[1].gres == 'gpu:a10g:8'
            assert result[1].cpus == 192
            assert result[1].memory_gb == 768
            assert result[1].partition == 'gpu nodes (RESERVED)'

            assert result[2].node == 'node3'
            assert result[2].state == 'alloc'
            assert result[2].gres == '(null)'
            assert result[2].cpus == 4
            assert result[2].memory_gb == 32
            assert result[2].partition == 'tpu nodes'


class TestCheckJobHasNodes:
    """Test SlurmClient.check_job_has_nodes()."""

    def test_returns_true_when_nodes_allocated(self):
        """Test returns True when squeue returns node names."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, 'node1,node2', '')
            assert client.check_job_has_nodes('12345') is True
            mock_run.assert_called_once_with(
                'squeue -h --jobs 12345 -o "%N"',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )

    def test_returns_false_when_no_nodes(self):
        """Test returns False when squeue returns empty output."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, '', '')
            assert client.check_job_has_nodes('12345') is False

    def test_returns_false_on_command_failure(self):
        """Test returns False when squeue command fails."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (1, '', 'error')
            assert client.check_job_has_nodes('12345') is False


class TestGetJobsStateByName:
    """Test SlurmClient.get_jobs_state_by_name()."""

    def test_get_jobs_state_by_name_single_running(self):
        """Test parsing single RUNNING job state."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        mock_output = 'RUNNING\n'
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, mock_output, '')

            result = client.get_jobs_state_by_name('sky-3a5e-pilot-9b1gdacf')
            mock_run.assert_called_once_with(
                'squeue -h --name sky-3a5e-pilot-9b1gdacf -o "%T"',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )

            assert result == ['RUNNING']

    def test_get_jobs_state_by_name_multiple_jobs(self):
        """Test parsing multiple jobs with different states."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        mock_output = 'RUNNING\nPENDING\nRUNNING\n'
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, mock_output, '')

            result = client.get_jobs_state_by_name('sky-test-job')
            mock_run.assert_called_once_with(
                'squeue -h --name sky-test-job -o "%T"',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )

            assert result == ['RUNNING', 'PENDING', 'RUNNING']


class TestSlurmClientInit:
    """Test SlurmClient.__init__()."""

    def test_init_local_execution_mode(self):
        """Test that is_inside_slurm_cluster=True uses LocalProcessCommandRunner."""
        from sky.utils import command_runner
        client = slurm.SlurmClient(is_inside_slurm_cluster=True)
        assert isinstance(client._runner,
                          command_runner.LocalProcessCommandRunner)

    def test_init_remote_execution_mode(self):
        """Test that default init uses SSHCommandRunner."""
        from sky.utils import command_runner
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
        )
        assert isinstance(client._runner, command_runner.SSHCommandRunner)


class TestGetJobNodes:
    """Test SlurmClient.get_job_nodes()."""

    def test_get_job_nodes_returns_nodes_and_ips(self):
        """Test that get_job_nodes returns parsed nodes and IPs."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, 'node1 10.0.0.1\nnode2 10.0.0.2', '')

            nodes, node_ips = client.get_job_nodes('12345')

            assert nodes == ['node1', 'node2']
            assert node_ips == ['10.0.0.1', '10.0.0.2']
            assert mock_run.call_count == 1

    def test_get_job_nodes_resolves_hostnames_via_login_node(self):
        """Test hostnames are resolved via getent ahostsv4 on the login node."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.side_effect = [
                # First call: squeue output with hostnames
                (0, 'worker-1 worker-1\nworker-10 worker-10', ''),
                # Second call: resolve loop output (hostname ip per line)
                (0, 'worker-1 10.20.30.1\nworker-10 10.20.30.10', ''),
            ]

            nodes, node_ips = client.get_job_nodes('12345')

            assert nodes == ['worker-1', 'worker-10']
            assert node_ips == ['10.20.30.1', '10.20.30.10']

            # Verify only 2 SSH calls were made (not 1 + N)
            assert mock_run.call_count == 2
            # Verify the resolve command was called with both hostnames
            second_call = mock_run.call_args_list[1][0][0]
            assert 'for h in worker-1 worker-10' in second_call
            assert 'getent ahostsv4' in second_call

    def test_get_job_nodes_hostname_resolution_failure(self):
        """Test error handling when hostname resolution fails."""

        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        # One hostname resolves, one fails (getent returns empty)
        resolve_output = (f'worker-1 10.20.30.1\n'
                          f'worker-10 {slurm._UNRESOLVED_HOSTNAME_MARKER}')

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.side_effect = [
                # First call: squeue output with hostnames
                (0, 'worker-1 worker-1\nworker-10 worker-10', ''),
                # Second call: resolve loop output with one UNRESOLVED
                (0, resolve_output, ''),
            ]

            with pytest.raises(RuntimeError,
                               match='Failed to resolve hostnames'):
                client.get_job_nodes('12345')


class TestGetAllJobsGres:
    """Test SlurmClient.get_all_jobs_gres()."""

    def test_get_all_jobs_gres_expansion(self):
        """Test parsing and expanding multi-node jobs using py-hostlist."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        squeue_output = (f'node01{slurm.SEP}gpu:h100:4\n'
                         f'node01{slurm.SEP}N/A\n'
                         f'node01,node03{slurm.SEP}gpu:h100:1\n'
                         f'node[02-03,06]{slurm.SEP}gpu:h100:2')

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, squeue_output, '')

            result = client.get_all_jobs_gres()

            # Verify squeue was called
            mock_run.assert_called_once_with(
                f'squeue -h --states=running,completing -o "%N{slurm.SEP}%b"',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )

            assert len(result) == 4
            assert result['node01'] == ['gpu:h100:4', 'gpu:h100:1']
            assert result['node02'] == ['gpu:h100:2']
            assert result['node03'] == ['gpu:h100:1', 'gpu:h100:2']
            assert result['node06'] == ['gpu:h100:2']


class TestParseMaxtime:
    """Test _parse_maxtime()."""

    def test_parse_maxtime_unlimited(self):
        """Test parsing UNLIMITED MaxTime returns None."""
        line = (
            'PartitionName=ml.g5.2xlarge AllowGroups=ALL AllowAccounts=ALL '
            'AllowQos=ALL AllocNodes=ALL Default=NO QoS=N/A DefaultTime=NONE '
            'DisableRootJobs=NO ExclusiveUser=NO ExclusiveTopo=NO GraceTime=0 '
            'Hidden=NO MaxNodes=UNLIMITED MaxTime=UNLIMITED MinNodes=0 LLN=NO '
            'MaxCPUsPerNode=UNLIMITED MaxCPUsPerSocket=UNLIMITED Nodes=ip-172-3-132-97,ip-172-3-168-59 '
            'PriorityJobFactor=1 PriorityTier=1 RootOnly=NO ReqResv=NO OverSubscribe=NO OverTimeLimit=NONE '
            'PreemptMode=OFF State=UP TotalCPUs=96 TotalNodes=2 SelectTypeParameters=NONE JobDefaults=(null) '
            'DefMemPerNode=UNLIMITED MaxMemPerNode=UNLIMITED TRES=cpu=96,mem=768G,node=2,billing=96,gres/gpu=8'
        )
        result = slurm._parse_maxtime(line)
        assert result is None

    def test_parse_maxtime_time_only(self):
        """Test parsing time format without days."""
        line = 'PartitionName=dev MaxTime=12:30:05 Default=YES'
        result = slurm._parse_maxtime(line)
        # 12*3600 + 30*60 + 5 = 43200 + 1800 + 5 = 45005
        assert result == 45005

    def test_parse_maxtime_with_days(self):
        """Test parsing time format with days."""
        line = 'PartitionName=dev MaxTime=2-12:30:05 Default=YES'
        result = slurm._parse_maxtime(line)
        # 2*86400 + 12*3600 + 30*60 + 5 = 172800 + 43200 + 1800 + 5 = 217805
        assert result == 217805

    def test_parse_maxtime_single_digit_minutes(self):
        """Test parsing time with single digit minutes (padded to 2 digits)."""
        # Note: The regex requires 2-digit minutes, so "05" is used
        line = 'PartitionName=dev MaxTime=10:05:30 Default=YES'
        result = slurm._parse_maxtime(line)
        # 10*3600 + 5*60 + 30 = 36000 + 300 + 30 = 36330
        assert result == 36330

    def test_parse_maxtime_single_digit_seconds(self):
        """Test parsing time with single digit seconds (padded to 2 digits)."""
        # Note: The regex requires 2-digit seconds, so "05" is used
        line = 'PartitionName=dev MaxTime=10:30:05 Default=YES'
        result = slurm._parse_maxtime(line)
        # 10*3600 + 30*60 + 5 = 36000 + 1800 + 5 = 37805
        assert result == 37805

    def test_parse_maxtime_zero_time(self):
        """Test parsing zero time."""
        line = 'PartitionName=dev MaxTime=00:00:00 Default=YES'
        result = slurm._parse_maxtime(line)
        assert result == 0

    def test_parse_maxtime_large_days(self):
        """Test parsing time with large number of days."""
        line = 'PartitionName=dev MaxTime=300-23:59:59 Default=YES'
        result = slurm._parse_maxtime(line)
        # 300*86400 + 23*3600 + 59*60 + 59 = 25920000 + 82800 + 3540 + 59 = 26006399
        assert result == 26006399

    def test_parse_maxtime_no_match(self):
        """Test parsing line without MaxTime returns None."""
        line = 'PartitionName=dev Default=YES Nodes=node1'
        result = slurm._parse_maxtime(line)
        assert result is None


class TestGetProctrackType:
    """Test SlurmClient.get_proctrack_type()."""

    @pytest.mark.parametrize(
        'mock_output,expected',
        [
            # Standard output with padding
            ('ProctrackType           = proctrack/cgroup\n', 'cgroup'),
            ('ProctrackType           = proctrack/linuxproc\n', 'linuxproc'),
            ('ProctrackType           = proctrack/pgid\n', 'pgid'),
            # Minimal spacing
            ('ProctrackType=proctrack/cgroup\n', 'cgroup'),
            # No match
            ('SomeOtherConfig = value\n', None),
            ('', None),
        ])
    def test_get_proctrack_type_parsing(self, mock_output, expected):
        """Test parsing various proctrack type outputs."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, mock_output, '')

            result = client.get_proctrack_type()
            mock_run.assert_called_once_with(
                'scontrol show config | grep -i "^ProctrackType"',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )

            assert result == expected

    def test_get_proctrack_type_command_failure(self):
        """Test handling command failure returns None."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (1, '', 'command not found')

            result = client.get_proctrack_type()

            assert result is None
