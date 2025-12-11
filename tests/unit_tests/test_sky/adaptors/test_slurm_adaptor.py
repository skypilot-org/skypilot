"""Unit tests for Slurm adaptor."""

import time
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


class TestWaitForJobNodes:
    """Test SlurmClient.wait_for_job_nodes()."""

    def test_wait_for_job_nodes_uses_default_timeout(self):
        """Test that wait_for_job_nodes uses default timeout of 10 seconds."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        job_id = '12345'
        start_time = time.time()

        # Mock get_job_state to return PENDING, then RUNNING
        # Mock squeue to return empty initially, then nodes
        with mock.patch.object(client, 'get_job_state') as mock_get_state, \
             mock.patch.object(client._runner, 'run') as mock_run:
            mock_get_state.side_effect = ['PENDING', 'PENDING', 'RUNNING']
            # First two calls return empty (no nodes), third returns nodes
            mock_run.side_effect = [
                (0, '', ''),  # No nodes allocated yet
                (0, '', ''),  # Still no nodes
                (0, 'node1,node2', ''),  # Nodes allocated
            ]

            # Should succeed quickly since nodes are allocated
            client.wait_for_job_nodes(
                job_id, timeout=slurm._SLURM_DEFAULT_PROVISION_TIMEOUT)

            # Verify it didn't wait the full default timeout
            elapsed = time.time() - start_time
            assert elapsed < 5, 'Should complete quickly when nodes are allocated'

    def test_wait_for_job_nodes_uses_custom_timeout(self):
        """Test that wait_for_job_nodes uses custom timeout when provided."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        job_id = '12345'
        custom_timeout = 2

        # Mock get_job_state to always return PENDING
        # Mock squeue to always return empty (no nodes)
        with mock.patch.object(client, 'get_job_state') as mock_get_state, \
             mock.patch.object(client._runner, 'run') as mock_run, \
             mock.patch('time.sleep') as mock_sleep:
            mock_get_state.return_value = 'PENDING'
            mock_run.return_value = (0, '', '')  # No nodes allocated

            start_time = time.time()
            try:
                client.wait_for_job_nodes(job_id, timeout=custom_timeout)
                assert False, 'Should raise TimeoutError'
            except TimeoutError as e:
                assert f'{custom_timeout} seconds' in str(e)
                # Verify it waited approximately the custom timeout
                elapsed = time.time() - start_time
                # Allow some margin for test execution time
                assert custom_timeout <= elapsed < (custom_timeout * 1.5)

    def test_wait_for_job_nodes_raises_on_job_termination(self):
        """Test that wait_for_job_nodes raises when job terminates."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        job_id = '12345'

        with mock.patch.object(client, 'get_job_state') as mock_get_state:
            mock_get_state.return_value = 'FAILED'

            with pytest.raises(RuntimeError,
                               match='terminated with state FAILED'):
                client.wait_for_job_nodes(
                    job_id, timeout=slurm._SLURM_DEFAULT_PROVISION_TIMEOUT)


class TestGetJobNodes:
    """Test SlurmClient.get_job_nodes()."""

    def test_get_job_nodes_passes_timeout_to_wait_for_job_nodes(self):
        """Test that get_job_nodes passes timeout to wait_for_job_nodes."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        job_id = '12345'
        custom_timeout = 20

        with mock.patch.object(client, 'wait_for_job_nodes') as mock_wait, \
             mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, 'node1 10.0.0.1\nnode2 10.0.0.2', '')

            client.get_job_nodes(job_id, wait=True, timeout=custom_timeout)

            # Verify wait_for_job_nodes was called with the custom timeout
            mock_wait.assert_called_once_with(job_id, timeout=custom_timeout)

    def test_get_job_nodes_uses_default_timeout_when_not_provided(self):
        """Test that get_job_nodes uses default timeout when not provided."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        job_id = '12345'

        with mock.patch.object(client, 'wait_for_job_nodes') as mock_wait, \
             mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, 'node1 10.0.0.1\nnode2 10.0.0.2', '')

            client.get_job_nodes(job_id, wait=True)

            # Verify wait_for_job_nodes was called with None (which becomes default)
            mock_wait.assert_called_once_with(
                job_id, timeout=slurm._SLURM_DEFAULT_PROVISION_TIMEOUT)

    def test_get_job_nodes_skips_wait_when_wait_false(self):
        """Test that get_job_nodes skips waiting when wait=False."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        job_id = '12345'

        with mock.patch.object(client, 'wait_for_job_nodes') as mock_wait, \
             mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, 'node1 10.0.0.1\nnode2 10.0.0.2', '')

            client.get_job_nodes(job_id, wait=False)

            # Verify wait_for_job_nodes was not called
            mock_wait.assert_not_called()
