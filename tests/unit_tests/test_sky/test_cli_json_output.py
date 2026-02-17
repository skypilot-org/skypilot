"""Tests for CLI -o json output support.

Tests for sky status -o json, sky jobs queue -o json, sky gpus list -o json,
sky api status -o json, and sky queue -o json.
"""
import json
from unittest import mock

from click import testing as cli_testing

from sky.catalog import common as catalog_common
from sky.client import sdk
from sky.client.cli import command
from sky.jobs import state as job_state
from sky.schemas.api import responses
from sky.server.requests import payloads
from sky.skylet import job_lib
from sky.utils import status_lib


class TestStatusJsonOutput:
    """Tests for `sky status -o json` output format."""

    def _make_cluster_record(self, name='mycluster', **kwargs):
        defaults = dict(
            name=name,
            launched_at=1700000000,
            handle=None,
            last_use='sky launch',
            status=status_lib.ClusterStatus.UP,
            autostop=0,
            to_down=False,
            cluster_hash='abc123',
            cluster_ever_up=True,
            user_hash='user1',
            user_name='alice',
            workspace='default',
            is_managed=False,
            nodes=1,
            cloud='AWS',
            region='us-east-1',
            resources_str='1x AWS(m5.xlarge)',
        )
        defaults.update(kwargs)
        return responses.StatusResponse(**defaults)

    def test_json_output_valid(self, monkeypatch):
        """Test that -o json produces valid JSON."""
        records = [self._make_cluster_record()]

        monkeypatch.setattr(
            'sky.client.cli.command'
            '._get_cluster_records_and_set_ssh_config',
            lambda *a, **kw: records)

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.status, ['-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert len(parsed) == 1
        assert parsed[0]['name'] == 'mycluster'
        assert parsed[0]['status'] == 'UP'
        assert parsed[0]['cloud'] == 'AWS'

    def test_json_output_excludes_handle_and_credentials(self, monkeypatch):
        """Test that handle and credentials are excluded from JSON."""
        records = [
            self._make_cluster_record(
                handle='not-serializable',
                credentials={'secret': 'key'},
            )
        ]

        monkeypatch.setattr(
            'sky.client.cli.command'
            '._get_cluster_records_and_set_ssh_config',
            lambda *a, **kw: records)

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.status, ['-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert 'handle' not in parsed[0]
        assert 'credentials' not in parsed[0]

    def test_json_output_multiple_clusters(self, monkeypatch):
        """Test JSON output with multiple clusters."""
        records = [
            self._make_cluster_record(name='cluster-1',
                                      status=status_lib.ClusterStatus.UP),
            self._make_cluster_record(name='cluster-2',
                                      status=status_lib.ClusterStatus.STOPPED),
        ]

        monkeypatch.setattr(
            'sky.client.cli.command'
            '._get_cluster_records_and_set_ssh_config',
            lambda *a, **kw: records)

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.status, ['-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert len(parsed) == 2
        assert parsed[0]['name'] == 'cluster-1'
        assert parsed[1]['status'] == 'STOPPED'

    def test_json_output_no_table_text(self, monkeypatch):
        """Test that -o json suppresses table output."""
        monkeypatch.setattr(
            'sky.client.cli.command'
            '._get_cluster_records_and_set_ssh_config', lambda *a, **kw: [])

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.status, ['-o', 'json'])

        assert result.exit_code == 0, result.output
        assert 'Clusters' not in result.output
        assert 'Managed jobs' not in result.output


class TestJobsQueueJsonOutput:
    """Tests for `sky jobs queue -o json` output format."""

    def _make_job_record(self, job_id=1, **kwargs):
        defaults = dict(
            job_id=job_id,
            task_id=0,
            job_name='my-job',
            status=job_state.ManagedJobStatus.RUNNING,
            submitted_at=1700000000.0,
            workspace='default',
            resources='1x AWS(p3.2xlarge, V100:1)',
            cloud='AWS',
            region='us-east-1',
        )
        defaults.update(kwargs)
        return responses.ManagedJobRecord(**defaults)

    def test_json_output_valid(self, monkeypatch):
        """Test that -o json produces valid JSON."""
        records = [self._make_job_record()]
        # V2 format: (records, total, status_counts, offset)
        mock_result = (records, 1, {'RUNNING': 1}, 0)

        monkeypatch.setattr(
            'sky.client.cli.utils.get_managed_job_queue', lambda **kw:
            ('req-1', mock.MagicMock(v2=lambda: True)))
        monkeypatch.setattr('sky.jobs.pool_status', lambda **kw: None)
        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            lambda *a, **kw: mock_result)

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.jobs_queue, ['-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert len(parsed) == 1
        assert parsed[0]['job_id'] == 1
        assert parsed[0]['job_name'] == 'my-job'
        assert parsed[0]['status'] == 'RUNNING'

    def test_json_output_multiple_jobs(self, monkeypatch):
        """Test JSON output with multiple jobs."""
        records = [
            self._make_job_record(job_id=1, job_name='train-1'),
            self._make_job_record(job_id=2,
                                  job_name='train-2',
                                  status=job_state.ManagedJobStatus.SUCCEEDED),
        ]
        mock_result = (records, 2, {'RUNNING': 1, 'SUCCEEDED': 1}, 0)

        monkeypatch.setattr(
            'sky.client.cli.utils.get_managed_job_queue', lambda **kw:
            ('req-1', mock.MagicMock(v2=lambda: True)))
        monkeypatch.setattr('sky.jobs.pool_status', lambda **kw: None)
        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            lambda *a, **kw: mock_result)

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.jobs_queue, ['-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert len(parsed) == 2
        assert parsed[1]['status'] == 'SUCCEEDED'


class TestGpusListJsonOutput:
    """Tests for `sky gpus list -o json` output format."""

    def test_json_output_valid(self):
        """Test that -o json produces valid JSON."""
        mock_result = {
            'V100': [
                catalog_common.InstanceTypeInfo(
                    cloud='AWS',
                    instance_type='p3.2xlarge',
                    accelerator_name='V100',
                    accelerator_count=1,
                    cpu_count=8,
                    device_memory=16.0,
                    memory=61.0,
                    price=3.06,
                    spot_price=0.918,
                    region='us-east-1',
                )
            ],
        }

        with mock.patch.object(sdk, 'enabled_clouds',
                               return_value='mock_req'), \
             mock.patch.object(sdk, 'get', return_value=[]), \
             mock.patch.object(sdk, 'list_accelerators',
                               return_value='mock_req2'), \
             mock.patch.object(sdk, 'stream_and_get',
                               return_value=mock_result):
            runner = cli_testing.CliRunner()
            result = runner.invoke(command.gpus_list, ['V100', '-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert 'V100' in parsed
        assert len(parsed['V100']) == 1
        assert parsed['V100'][0]['cloud'] == 'AWS'
        assert parsed['V100'][0]['instance_type'] == 'p3.2xlarge'
        assert parsed['V100'][0]['price'] == 3.06

    def test_json_output_empty(self):
        """Test JSON output when no accelerators found."""
        with mock.patch.object(sdk, 'enabled_clouds',
                               return_value='mock_req'), \
             mock.patch.object(sdk, 'get', return_value=[]), \
             mock.patch.object(sdk, 'list_accelerators',
                               return_value='mock_req2'), \
             mock.patch.object(sdk, 'stream_and_get',
                               return_value={}):
            runner = cli_testing.CliRunner()
            result = runner.invoke(command.gpus_list,
                                   ['NonExistentGPU', '-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed == {}


class TestApiStatusJsonOutput:
    """Tests for `sky api status -o json` output format."""

    def _make_request_payload(self, request_id='req-1', **kwargs):
        defaults = dict(
            request_id=request_id,
            name='status',
            entrypoint='sky status',
            request_body='{}',
            status='RUNNING',
            created_at=1700000000.0,
            user_id='user1',
            return_value='',
            error='',
            pid=12345,
            schedule_type='NORMAL',
            user_name='alice',
        )
        defaults.update(kwargs)
        return payloads.RequestPayload(**defaults)

    def test_json_output_valid(self, monkeypatch):
        """Test that -o json produces valid JSON."""
        records = [self._make_request_payload()]

        monkeypatch.setattr('sky.client.sdk.api_status',
                            lambda *a, **kw: records)

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.api_status, ['-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert len(parsed) == 1
        assert parsed[0]['request_id'] == 'req-1'
        assert parsed[0]['name'] == 'status'
        assert parsed[0]['status'] == 'RUNNING'

    def test_json_output_multiple_requests(self, monkeypatch):
        """Test JSON output with multiple requests."""
        records = [
            self._make_request_payload(request_id='req-1', name='launch'),
            self._make_request_payload(request_id='req-2',
                                       name='down',
                                       status='SUCCEEDED'),
        ]

        monkeypatch.setattr('sky.client.sdk.api_status',
                            lambda *a, **kw: records)

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.api_status, ['-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert len(parsed) == 2
        assert parsed[0]['request_id'] == 'req-1'
        assert parsed[1]['status'] == 'SUCCEEDED'

    def test_json_output_empty(self, monkeypatch):
        """Test JSON output with no requests."""
        monkeypatch.setattr('sky.client.sdk.api_status', lambda *a, **kw: [])

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.api_status, ['-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed == []


class TestQueueJsonOutput:
    """Tests for `sky queue -o json` output format."""

    def _make_job_record(self, job_id=1, **kwargs):
        defaults = dict(
            job_id=job_id,
            job_name='train',
            username='alice',
            user_hash='user1',
            submitted_at=1700000000.0,
            resources='1x AWS(p3.2xlarge)',
            status=job_lib.JobStatus.RUNNING,
            log_path='/tmp/log',
        )
        defaults.update(kwargs)
        return responses.ClusterJobRecord(**defaults)

    def test_json_output_valid(self, monkeypatch):
        """Test that -o json produces valid JSON."""
        records = [self._make_job_record()]

        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            lambda *a, **kw: records)
        monkeypatch.setattr('sky.client.sdk.queue', lambda *a, **kw: 'mock_req')
        monkeypatch.setattr(
            'sky.client.cli.command._get_cluster_records_and_set_ssh_config',
            lambda *a, **kw: [])

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.queue, ['mycluster', '-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert 'mycluster' in parsed
        assert len(parsed['mycluster']) == 1
        assert parsed['mycluster'][0]['job_id'] == 1
        assert parsed['mycluster'][0]['status'] == 'RUNNING'

    def test_json_output_multiple_clusters(self, monkeypatch):
        """Test JSON output across multiple clusters."""
        records_a = [self._make_job_record(job_id=1, job_name='train-a')]
        records_b = [
            self._make_job_record(job_id=2,
                                  job_name='train-b',
                                  status=job_lib.JobStatus.SUCCEEDED)
        ]
        call_count = {'n': 0}

        def mock_stream_and_get(*a, **kw):
            call_count['n'] += 1
            if call_count['n'] == 1:
                return records_a
            return records_b

        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock_stream_and_get)
        monkeypatch.setattr('sky.client.sdk.queue', lambda *a, **kw: 'mock_req')
        monkeypatch.setattr(
            'sky.client.cli.command._get_cluster_records_and_set_ssh_config',
            lambda *a, **kw: [])

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.queue,
                               ['cluster-a', 'cluster-b', '-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert 'cluster-a' in parsed
        assert 'cluster-b' in parsed
        assert parsed['cluster-a'][0]['job_name'] == 'train-a'
        assert parsed['cluster-b'][0]['status'] == 'SUCCEEDED'

    def test_json_output_empty(self, monkeypatch):
        """Test JSON output when no clusters specified and none exist."""
        monkeypatch.setattr(
            'sky.client.cli.command._get_cluster_records_and_set_ssh_config',
            lambda *a, **kw: [])

        runner = cli_testing.CliRunner()
        result = runner.invoke(command.queue, ['-o', 'json'])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed == {}
