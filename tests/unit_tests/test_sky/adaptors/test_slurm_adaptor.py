"""Unit tests for Slurm adaptor."""

import base64
import unittest.mock as mock

import pytest

from sky import exceptions
from sky.adaptors import slurm
from sky.utils import command_runner as command_runner_lib


def _batch_output(*outputs):
    framed = ['SKYPILOT_SLURM_BATCH\n']
    for returncode, stdout, stderr in outputs:
        encoded_stdout = base64.b64encode(stdout.encode()).decode()
        encoded_stderr = base64.b64encode(stderr.encode()).decode()
        framed.append(
            f'{returncode} {len(encoded_stdout)} {len(encoded_stderr)}\n')
        framed.extend([encoded_stdout, encoded_stderr])
    return ''.join(framed)


class TestRunSlurmCmds:
    """Tests for the concurrent command transport and framing protocol."""

    @staticmethod
    def _client():
        return slurm.SlurmClient(ssh_host='localhost',
                                 ssh_port=22,
                                 ssh_user='root',
                                 ssh_key=None)

    def test_empty_command_list_skips_remote_invocation(self):
        client = self._client()
        with mock.patch.object(client._runner, 'run') as mock_run:
            assert not client._run_slurm_cmds([])
        mock_run.assert_not_called()

    def test_generated_script_and_parser_round_trip(self):
        client = self._client()
        client._runner = command_runner_lib.LocalProcessCommandRunner()

        results = client._run_slurm_cmds([
            'sleep 0.1; printf \'first\\nnœud\\n\'',
            'printf \'second error\\n\' >&2; exit 7',
        ])

        assert results == [(0, 'first\nnœud\n', ''), (7, '', 'second error\n')]

    def test_transport_preserves_frames_after_invalid_utf8_replacement(self):
        client = self._client()
        client._runner = command_runner_lib.LocalProcessCommandRunner()

        results = client._run_slurm_cmds([
            'python3 -c \'import os; os.write(1, bytes([255])); '
            'os.write(2, bytes([254]))\'',
            'printf \'second\'',
        ])

        assert results == [(0, '\ufffd', '\ufffd'), (0, 'second', '')]

    def test_transport_preserves_lines_filtered_by_command_runner(self):
        client = self._client()
        client._runner = command_runner_lib.LocalProcessCommandRunner()
        warning = 'bash: cannot set terminal process group\n'

        results = client._run_slurm_cmds([
            f'printf {warning!r}',
            f'printf {warning!r} >&2',
        ])

        assert results == [(0, warning, ''), (0, '', warning)]

    def test_byte_lengths_preserve_arbitrary_text_and_input_order(self):
        client = self._client()
        outputs = [
            (23, 'line 1\n0 2 3\nSKYPILOT_SLURM_BATCH\nnœud\x00',
             'warning\nstill warning'),
            (0, '', ''),
        ]
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, _batch_output(*outputs), '')

            results = client._run_slurm_cmds(['slow command', 'fast command'])

        assert results == outputs
        script = mock_run.call_args.args[0]
        assert script.index('slow command') < script.index('fast command')
        assert script.count(' ) &') == 2
        assert script.index('\nwait\n') < script.index('SKYPILOT_SLURM_BATCH')

    def test_parser_accepts_line_wrapped_base64_frames(self):
        client = self._client()
        expected = 'nœud\n' * 40
        encoded = base64.b64encode(expected.encode()).decode()
        wrapped = '\n'.join(encoded[offset:offset + 76]
                            for offset in range(0, len(encoded), 76))
        output = (f'SKYPILOT_SLURM_BATCH\n0 {len(wrapped)} 0\n'
                  f'{wrapped}')
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, output, '')

            results = client._run_slurm_cmds(['command'])

        assert results == [(0, expected, '')]

    @pytest.mark.parametrize(('output', 'error'), [
        ('', 'missing header'),
        ('not-the-protocol\n', 'missing header'),
        ('SKYPILOT_SLURM_BATCH\n\ufffd', 'non-ASCII transport output'),
        ('SKYPILOT_SLURM_BATCH\n', 'missing frame header'),
        ('SKYPILOT_SLURM_BATCH\ninvalid\n', 'invalid frame header'),
        ('SKYPILOT_SLURM_BATCH\n0 1\nx', 'invalid frame header'),
        ('SKYPILOT_SLURM_BATCH\n0 -1 0\n', 'invalid frame header'),
        ('SKYPILOT_SLURM_BATCH\n0 2 0\nx', 'truncated frame'),
        ('SKYPILOT_SLURM_BATCH\n0 1 0\n?', 'invalid encoded output'),
        ('SKYPILOT_SLURM_BATCH\n0 0 0\nextra', 'trailing data'),
    ])
    def test_rejects_malformed_framing(self, output, error):
        client = self._client()
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, output, '')

            with pytest.raises(RuntimeError, match=error):
                client._run_slurm_cmds(['command'])

    def test_outer_command_failure_is_not_parsed_as_a_frame(self):
        client = self._client()
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (255, '', 'connection lost')

            with pytest.raises(exceptions.CommandError) as exc_info:
                client._run_slurm_cmds(['command'])

        assert exc_info.value.returncode == 255


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


class TestInventorySnapshot:
    """Tests for batched Slurm inventory collection."""

    def test_get_node_inventory_uses_one_remote_invocation(self):
        client = slurm.SlurmClient(ssh_host='localhost',
                                   ssh_port=22,
                                   ssh_user='root',
                                   ssh_key=None)
        sinfo_output = (f'nœud1{slurm.SEP}mix{slurm.SEP}gpu:h100:8{slurm.SEP}64'
                        f'{slurm.SEP}819200{slurm.SEP}gpu\n')
        details_output = ('NodeName=nœud1 CPUAlloc=32 CPUTot=64 '
                          'CfgTRES=gres/gpu=8 AllocTRES=gres/gpu=4\n')
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0,
                                     _batch_output((0, sinfo_output, ''),
                                                   (0, details_output, '')), '')

            node_infos, node_details = client.get_node_inventory()

        mock_run.assert_called_once()
        script = mock_run.call_args.args[0]
        assert 'sinfo -h --Node' in script
        assert 'scontrol show node -o' in script
        assert script.count(' ) &') == 2
        assert node_infos[0].node == 'nœud1'
        assert node_details['nœud1']['CPUAlloc'] == '32'

    def test_get_inventory_snapshot_parses_all_sections(self):
        client = slurm.SlurmClient(ssh_host='localhost',
                                   ssh_port=22,
                                   ssh_user='root',
                                   ssh_key=None)
        sinfo_output = (f'node1{slurm.SEP}mix{slurm.SEP}gpu:h100:8'
                        f'{slurm.SEP}64{slurm.SEP}819200{slurm.SEP}gpu\n')
        details_output = ('NodeName=node1 CPUAlloc=32 CPUTot=64 '
                          'CfgTRES=gres/gpu=8 AllocTRES=gres/gpu=4\n')
        jobs_output = (f'123{slurm.SEP}train{slurm.SEP}alice{slurm.SEP}'
                       f'node1{slurm.SEP}gpu:h100:4\n')
        partitions_output = ('PartitionName=gpu Default=YES '
                             'DefaultTime=01:00:00 MaxTime=UNLIMITED\n')
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0,
                                     _batch_output(
                                         (0, sinfo_output, ''),
                                         (0, details_output, ''),
                                         (0, jobs_output, ''),
                                         (0, partitions_output, '')), '')

            snapshot = client.get_inventory_snapshot()

        mock_run.assert_called_once()
        script = mock_run.call_args.args[0]
        for command in ('sinfo -h --Node', 'scontrol show node -o',
                        'squeue -h --states=running,completing',
                        'scontrol show partitions -o'):
            assert command in script
        assert script.count(' ) &') == 4
        assert snapshot.node_infos[0].node == 'node1'
        assert snapshot.node_details['node1']['CPUAlloc'] == '32'
        assert snapshot.jobs == {
            'node1': [
                slurm.JobGresInfo(job_id='123',
                                  job_name='train',
                                  user='alice',
                                  gres_str='gpu:h100:4')
            ]
        }
        assert snapshot.partitions == [
            slurm.SlurmPartition(name='gpu',
                                 is_default=True,
                                 maxtime=None,
                                 default_time='01:00:00')
        ]

    def test_get_inventory_snapshot_keeps_optional_failures_isolated(self):
        client = slurm.SlurmClient(ssh_host='localhost',
                                   ssh_port=22,
                                   ssh_user='root',
                                   ssh_key=None)
        sinfo_output = (f'node1{slurm.SEP}idle{slurm.SEP}(null)'
                        f'{slurm.SEP}4{slurm.SEP}16384{slurm.SEP}cpu\n')
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0,
                                     _batch_output(
                                         (0, sinfo_output, ''),
                                         (1, '', 'scontrol failed'),
                                         (1, '', 'squeue failed'),
                                         (1, '', 'partitions failed')), '')

            snapshot = client.get_inventory_snapshot()

        assert len(snapshot.node_infos) == 1
        assert snapshot.node_details == {}
        assert snapshot.jobs is None
        assert snapshot.partitions is None

    def test_get_inventory_snapshot_requires_node_information(self):
        client = slurm.SlurmClient(ssh_host='localhost',
                                   ssh_port=22,
                                   ssh_user='root',
                                   ssh_key=None)
        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0,
                                     _batch_output((1, '', 'sinfo failed'),
                                                   (0, '', ''), (0, '', ''),
                                                   (0, '', '')), '')

            with pytest.raises(exceptions.CommandError) as exc_info:
                client.get_inventory_snapshot()

        assert exc_info.value.returncode == 1


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


class TestGetJobState:
    """Test SlurmClient.get_job_state()."""

    def test_get_job_state_with_only_job_state_flag(self):
        """Test that get_job_state uses --only-job-state when supported."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, 'RUNNING\n', '')
            result = client.get_job_state('12345')
            mock_run.assert_called_once_with(
                'squeue -h --only-job-state --jobs 12345 -o "%T"',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )
            assert result == 'RUNNING'

    def test_get_job_state_falls_back_on_old_slurm(self):
        """Test fallback when --only-job-state is not supported (Slurm < 21.08)."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.side_effect = [
                (1, '', "squeue: unrecognized option '--only-job-state'"),
                (0, 'PENDING\n', ''),
            ]
            result = client.get_job_state('12345')
            assert mock_run.call_count == 2
            mock_run.assert_called_with(
                'squeue -h --jobs 12345 -o "%T"',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )
            assert result == 'PENDING'

    def test_get_job_state_returns_none_for_empty_output(self):
        """Test returns None when job is not found."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, '', '')
            result = client.get_job_state('99999')
            assert result is None


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


class TestGetAllJobsInfo:
    """Test SlurmClient.get_all_jobs_info()."""

    def test_get_all_jobs_info_expansion(self):
        """Multi-node jobs fan out to every node, keeping job identity."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        squeue_output = (
            f'101{slurm.SEP}train a{slurm.SEP}alice{slurm.SEP}node01'
            f'{slurm.SEP}gpu:h100:4\n'
            f'102{slurm.SEP}cpu-only{slurm.SEP}bob{slurm.SEP}node01'
            f'{slurm.SEP}N/A\n'
            f'103{slurm.SEP}pretrain{slurm.SEP}bob{slurm.SEP}node[02-03]'
            f'{slurm.SEP}gpu:h100:2\n'
            f'malformed line without separators\n')

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, squeue_output, '')

            result = client.get_all_jobs_info()

            mock_run.assert_called_once_with(
                f'squeue -h --states=running,completing '
                f'-o "%i{slurm.SEP}%j{slurm.SEP}%u{slurm.SEP}%N{slurm.SEP}%b"',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )

            # Job 102 (no GRES) and the malformed line are skipped.
            assert set(result.keys()) == {'node01', 'node02', 'node03'}
            assert result['node01'] == [
                slurm.JobGresInfo(job_id='101',
                                  job_name='train a',
                                  user='alice',
                                  gres_str='gpu:h100:4')
            ]
            # Multi-node job 103 appears on both nodes with the same
            # per-node GRES.
            for node in ('node02', 'node03'):
                assert result[node] == [
                    slurm.JobGresInfo(job_id='103',
                                      job_name='pretrain',
                                      user='bob',
                                      gres_str='gpu:h100:2')
                ]

    def test_get_all_jobs_info_empty_queue(self):
        """Empty squeue output yields an empty mapping."""
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, '', '')

            assert client.get_all_jobs_info() == {}


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


class TestParseDefaultTime:
    """Test _parse_default_time()."""

    def test_parse_default_time_none(self):
        """DefaultTime=NONE returns None."""
        line = 'PartitionName=dev DefaultTime=NONE MaxTime=UNLIMITED'
        result = slurm._parse_default_time(line)
        assert result is None

    def test_parse_default_time_unlimited(self):
        """DefaultTime=UNLIMITED returns None (no useful default)."""
        line = 'PartitionName=dev DefaultTime=UNLIMITED MaxTime=UNLIMITED'
        result = slurm._parse_default_time(line)
        assert result is None

    def test_parse_default_time_hms(self):
        """Returns the raw hh:mm:ss string verbatim."""
        line = 'PartitionName=dev DefaultTime=01:00:00 MaxTime=12:00:00'
        result = slurm._parse_default_time(line)
        assert result == '01:00:00'

    def test_parse_default_time_with_days(self):
        line = 'PartitionName=dev DefaultTime=2-00:00:00 MaxTime=7-00:00:00'
        result = slurm._parse_default_time(line)
        assert result == '2-00:00:00'

    def test_parse_default_time_no_match(self):
        line = 'PartitionName=dev MaxTime=12:00:00'
        result = slurm._parse_default_time(line)
        assert result is None


class TestGetPartitionsInfoDefaultTime:
    """Verify get_partitions_info() populates the default_time field."""

    def test_populates_default_time(self):
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        mock_output = ('PartitionName=cpu Default=YES DefaultTime=01:00:00 '
                       'MaxTime=UNLIMITED\n'
                       'PartitionName=gpu Default=NO DefaultTime=NONE '
                       'MaxTime=02:00:00')

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, mock_output, '')
            result = client.get_partitions_info()

        assert len(result) == 2
        assert result[0].name == 'cpu'
        assert result[0].is_default is True
        assert result[0].maxtime is None  # UNLIMITED
        assert result[0].default_time == '01:00:00'
        assert result[1].name == 'gpu'
        assert result[1].is_default is False
        assert result[1].maxtime == 7200
        assert result[1].default_time is None


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


class TestGetAllNodeDetails:
    """Test SlurmClient.get_all_node_details()."""

    def test_parses_one_line_per_node(self):
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        mock_output = (
            'NodeName=node1 Arch=x86_64 CPUAlloc=8 CPUEfctv=72 CPUTot=72 '
            'CPULoad=3.50 Gres=gpu:gh200:1 RealMemory=430080 AllocMem=102400 '
            'FreeMem=421339 State=MIXED Partitions=all,gh200\n'
            'NodeName=node2 Arch=x86_64 CPUAlloc=0 CPUEfctv=2 CPUTot=2 '
            'CPULoad=N/A Gres=(null) RealMemory=14000 AllocMem=0 FreeMem=N/A '
            'State=DOWN* Partitions=dev\n')

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, mock_output, '')

            result = client.get_all_node_details()
            mock_run.assert_called_once_with(
                'scontrol show node -o',
                require_outputs=True,
                separate_stderr=True,
                stream_logs=False,
            )

        assert set(result.keys()) == {'node1', 'node2'}
        assert result['node1']['CPUAlloc'] == '8'
        assert result['node1']['CPUTot'] == '72'
        assert result['node1']['CPULoad'] == '3.50'
        assert result['node1']['AllocMem'] == '102400'
        assert result['node1']['FreeMem'] == '421339'
        assert result['node1']['State'] == 'MIXED'
        assert result['node2']['CPULoad'] == 'N/A'
        assert result['node2']['FreeMem'] == 'N/A'

    def test_skips_blank_lines_and_lines_without_node_name(self):
        client = slurm.SlurmClient(
            ssh_host='localhost',
            ssh_port=22,
            ssh_user='root',
            ssh_key=None,
        )

        mock_output = ('\n'
                       'NodeName=node1 CPUTot=8\n'
                       '   \n'
                       'Arch=x86_64 CPUTot=4\n')

        with mock.patch.object(client._runner, 'run') as mock_run:
            mock_run.return_value = (0, mock_output, '')
            result = client.get_all_node_details()

        assert list(result.keys()) == ['node1']
