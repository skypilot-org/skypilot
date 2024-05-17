import base64
import pickle
import tempfile
import textwrap
import time
from typing import Optional, Tuple

import click
from click import testing as cli_testing
import pytest

import sky
from sky import backends
from sky import cli
from sky import exceptions
from sky import global_user_state
from sky import jobs
from sky import serve
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import db_utils


def test_job_nonexist_strategy():
    """Test the nonexist recovery strategy."""
    task_yaml = textwrap.dedent("""\
        resources:
            cloud: aws
            use_spot: true
            job_recovery: nonexist""")
    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(task_yaml)
        f.flush()
        with pytest.raises(
                ValueError,
                match='is not supported. The strategy should be among'):
            sky.Task.from_yaml(f.name)


@pytest.fixture
def _mock_db_conn(monkeypatch, tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / 'state_testing.db'
    monkeypatch.setattr(
        global_user_state, '_DB',
        db_utils.SQLiteConn(str(db_path), global_user_state.create_table))


@pytest.fixture
def _mock_cluster_state(_mock_db_conn):
    assert 'state.db' not in global_user_state._DB.db_path
    handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster1',
        cluster_name_on_cloud='test-cluster1',
        cluster_yaml='/tmp/cluster1.yaml',
        launched_nodes=2,
        launched_resources=sky.Resources(sky.AWS(),
                                         instance_type='p3.2xlarge',
                                         region='us-east-1'),
    )
    global_user_state.add_or_update_cluster(
        'test-cluster1',
        handle,
        requested_resources={handle.launched_resources},
        ready=True)
    handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster2',
        cluster_name_on_cloud='test-cluster2',
        cluster_yaml='/tmp/cluster2.yaml',
        launched_nodes=1,
        launched_resources=sky.Resources(sky.GCP(),
                                         instance_type='a2-highgpu-4g',
                                         accelerators={'A100': 4},
                                         region='us-west1'),
    )
    global_user_state.add_or_update_cluster(
        'test-cluster2',
        handle,
        requested_resources={handle.launched_resources},
        ready=True)
    handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster3',
        cluster_name_on_cloud='test-cluster3',
        cluster_yaml='/tmp/cluster3.yaml',
        launched_nodes=4,
        launched_resources=sky.Resources(sky.Azure(),
                                         instance_type='Standard_D4s_v3',
                                         region='eastus'),
    )
    global_user_state.add_or_update_cluster(
        'test-cluster3',
        handle,
        requested_resources={handle.launched_resources},
        ready=False)


@pytest.fixture
def _mock_jobs_controller(_mock_db_conn):
    handle = backends.CloudVmRayResourceHandle(
        cluster_name=jobs.JOB_CONTROLLER_NAME,
        cluster_name_on_cloud=jobs.JOB_CONTROLLER_NAME,
        cluster_yaml='/tmp/jobs_controller.yaml',
        launched_nodes=1,
        launched_resources=sky.Resources(sky.AWS(),
                                         instance_type='m4.2xlarge',
                                         region='us-west-1'),
    )
    global_user_state.add_or_update_cluster(
        jobs.JOB_CONTROLLER_NAME,
        handle,
        requested_resources={handle.launched_resources},
        ready=True)


@pytest.fixture
def _mock_serve_controller(_mock_db_conn):
    handle = backends.CloudVmRayResourceHandle(
        cluster_name=serve.SKY_SERVE_CONTROLLER_NAME,
        cluster_name_on_cloud=serve.SKY_SERVE_CONTROLLER_NAME,
        cluster_yaml='/tmp/serve_controller.yaml',
        launched_nodes=1,
        launched_resources=sky.Resources(sky.AWS(),
                                         instance_type='m4.2xlarge',
                                         region='us-west-1'),
    )
    global_user_state.add_or_update_cluster(
        serve.SKY_SERVE_CONTROLLER_NAME,
        handle,
        requested_resources={handle.launched_resources},
        ready=True)


def mock_is_controller_accessible(
    controller: controller_utils.Controllers,
    stopped_message: str,
    non_existent_message: Optional[str] = None,
    exit_on_error: bool = False,
):
    record = global_user_state.get_cluster_from_name(
        controller.value.cluster_name)
    return record['handle']


class TestJobsOperations:
    """Test operations for managed jobs."""

    @pytest.mark.timeout(60)
    def test_down_jobs_controller(self, _mock_cluster_state,
                                  _mock_jobs_controller, monkeypatch):

        def mock_get_job_table_no_job(cls, handle, code, require_outputs,
                                      stream_logs,
                                      separate_stderr) -> Tuple[int, str, str]:
            return 0, common_utils.encode_payload([]), ''

        def mock_get_job_table_one_job(cls, handle, code, require_outputs,
                                       stream_logs,
                                       separate_stderr) -> Tuple[int, str, str]:
            return 0, common_utils.encode_payload([{
                'job_id': '1',
                'job_name': 'test_job',
                'resources': 'test',
                'status': 'RUNNING',
                'submitted_at': time.time(),
                'run_timestamp': str(time.time()),
                'start_at': time.time(),
                'end_at': time.time(),
                'last_recovered_at': None,
                'recovery_count': 0,
                'failure_reason': '',
                'managed_job_id': '1',
                'task_id': 0,
                'task_name': 'test_task',
                'job_duration': 20,
            }]), ''

        monkeypatch.setattr(
            'sky.backends.backend_utils.is_controller_accessible',
            mock_is_controller_accessible)

        monkeypatch.setattr(
            'sky.backends.cloud_vm_ray_backend.CloudVmRayBackend.run_on_head',
            mock_get_job_table_no_job)

        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.down, [jobs.JOB_CONTROLLER_NAME],
                                   input='n')
        assert 'WARNING: Tearing down the managed jobs controller.' in result.output, (
            result.exception, result.output, result.exc_info)
        assert isinstance(result.exception,
                          SystemExit), (result.exception, result.output)

        monkeypatch.setattr(
            'sky.backends.cloud_vm_ray_backend.CloudVmRayBackend.run_on_head',
            mock_get_job_table_one_job)
        result = cli_runner.invoke(cli.down, [jobs.JOB_CONTROLLER_NAME],
                                   input='n')
        assert 'WARNING: Tearing down the managed jobs controller.' in result.output, (
            result.exception, result.output, result.exc_info)
        assert isinstance(result.exception, exceptions.NotSupportedError)

        result = cli_runner.invoke(cli.down, ['sky-jobs-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.down, ['sky-jobs-con*', '-p'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.down, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit), result.exception
        assert 'Aborted' in result.output

        result = cli_runner.invoke(cli.down, ['-ap'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    @pytest.mark.timeout(60)
    def test_stop_jobs_controller(self, _mock_cluster_state,
                                  _mock_jobs_controller):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.stop, [jobs.JOB_CONTROLLER_NAME])
        assert result.exit_code == click.UsageError.exit_code
        assert (f'Stopping controller(s) \'{jobs.JOB_CONTROLLER_NAME}\' is '
                'currently not supported' in result.output)

        result = cli_runner.invoke(cli.stop, ['sky-jobs-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.stop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    @pytest.mark.timeout(60)
    def test_autostop_jobs_controller(self, _mock_cluster_state,
                                      _mock_jobs_controller):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.autostop, [jobs.JOB_CONTROLLER_NAME])
        assert result.exit_code == click.UsageError.exit_code
        assert ('Scheduling autostop on controller(s) '
                f'\'{jobs.JOB_CONTROLLER_NAME}\' is currently not supported'
                in result.output)

        result = cli_runner.invoke(cli.autostop, ['sky-jobs-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.autostop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    def test_cancel_on_jobs_controller(self, _mock_cluster_state,
                                       _mock_jobs_controller):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.cancel, [jobs.JOB_CONTROLLER_NAME, '-a'])
        assert result.exit_code == click.UsageError.exit_code
        assert 'Cancelling the jobs controller\'s jobs is not allowed.' in str(
            result.output)

    @pytest.mark.timeout(60)
    def test_cancel(self, _mock_db_conn):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.jobs_cancel, ['-a'])
        assert result.exit_code == 1
        assert controller_utils.Controllers.JOBS_CONTROLLER.value.default_hint_if_non_existent in str(
            result.output), (result.exception, result.output, result.exc_info)

    @pytest.mark.timeout(60)
    def test_logs(self, _mock_db_conn):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.jobs_logs, ['1'])
        assert result.exit_code == 1
        assert controller_utils.Controllers.JOBS_CONTROLLER.value.default_hint_if_non_existent in str(
            result.exception), (result.exception, result.output,
                                result.exc_info)

    @pytest.mark.timeout(60)
    def test_queue(self, _mock_db_conn):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.jobs_queue)
        assert result.exit_code == 0
        assert controller_utils.Controllers.JOBS_CONTROLLER.value.default_hint_if_non_existent in str(
            result.output), (result.exception, result.output, result.exc_info)


class TestServeOperations:
    """Test operations for services."""

    @pytest.mark.timeout(60)
    def test_down_serve_controller(self, _mock_cluster_state,
                                   _mock_serve_controller, monkeypatch):

        def mock_get_services_no_service(
                cls, handle, code, require_outputs, stream_logs,
                separate_stderr) -> Tuple[int, str, str]:
            return 0, common_utils.encode_payload([]), ''

        def mock_get_services_one_service(
                cls, handle, code, require_outputs, stream_logs,
                separate_stderr) -> Tuple[int, str, str]:
            service = {
                'name': 'test_service',
                'controller_job_id': 1,
                'uptime': 20,
                'status': 'RUNNING',
                'controller_port': 30001,
                'load_balancer_port': 30000,
                'policy': None,
                'requested_resources': sky.Resources(),
                'requested_resources_str': '',
                'replica_info': [],
            }
            return 0, common_utils.encode_payload([{
                k: base64.b64encode(pickle.dumps(v)).decode('utf-8')
                for k, v in service.items()
            }]), ''

        monkeypatch.setattr(
            'sky.backends.backend_utils.is_controller_accessible',
            mock_is_controller_accessible)

        monkeypatch.setattr(
            'sky.backends.cloud_vm_ray_backend.CloudVmRayBackend.run_on_head',
            mock_get_services_no_service)

        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.down, [serve.SKY_SERVE_CONTROLLER_NAME],
                                   input='n')
        assert 'Terminate sky serve controller:' in result.output, (
            result.exception, result.output, result.exc_info)
        assert isinstance(result.exception,
                          SystemExit), (result.exception, result.output)

        monkeypatch.setattr(
            'sky.backends.cloud_vm_ray_backend.CloudVmRayBackend.run_on_head',
            mock_get_services_one_service)
        result = cli_runner.invoke(cli.down, [serve.SKY_SERVE_CONTROLLER_NAME],
                                   input='n')
        assert isinstance(result.exception, exceptions.NotSupportedError)

        result = cli_runner.invoke(cli.down, ['sky-serve-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.down, ['sky-serve-con*', '-p'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.down, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit), result.exception
        assert 'Aborted' in result.output

        result = cli_runner.invoke(cli.down, ['-ap'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    @pytest.mark.timeout(60)
    def test_stop_serve_controller(self, _mock_cluster_state,
                                   _mock_serve_controller):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.stop, [serve.SKY_SERVE_CONTROLLER_NAME])
        assert result.exit_code == click.UsageError.exit_code
        assert (
            f'Stopping controller(s) \'{serve.SKY_SERVE_CONTROLLER_NAME}\' is '
            'currently not supported' in result.output)

        result = cli_runner.invoke(cli.stop, ['sky-serve-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.stop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    @pytest.mark.timeout(60)
    def test_autostop_serve_controller(self, _mock_cluster_state,
                                       _mock_serve_controller):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.autostop,
                                   [serve.SKY_SERVE_CONTROLLER_NAME])
        assert result.exit_code == click.UsageError.exit_code
        assert (
            'Scheduling autostop on controller(s) '
            f'\'{serve.SKY_SERVE_CONTROLLER_NAME}\' is currently not supported'
            in result.output)

        result = cli_runner.invoke(cli.autostop, ['sky-serve-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.autostop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    def test_cancel_on_serve_controller(self, _mock_cluster_state,
                                        _mock_serve_controller):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.cancel,
                                   [serve.SKY_SERVE_CONTROLLER_NAME, '-a'])
        assert result.exit_code == click.UsageError.exit_code
        assert 'Cancelling the sky serve controller\'s jobs is not allowed.' in str(
            result.output)

    @pytest.mark.timeout(60)
    def test_down(self, _mock_db_conn):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.serve_down, ['-a'])
        assert result.exit_code == 1
        assert (controller_utils.Controllers.SKY_SERVE_CONTROLLER.value.
                default_hint_if_non_existent
                in str(result.output)), (result.exception, result.output,
                                         result.exc_info)

    @pytest.mark.timeout(60)
    def test_logs(self, _mock_db_conn):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.serve_logs, ['test', '--controller'])
        assert controller_utils.Controllers.SKY_SERVE_CONTROLLER.value.default_hint_if_non_existent in str(
            result.exception), (result.exception, result.output,
                                result.exc_info)

    @pytest.mark.timeout(60)
    def test_status(self, _mock_db_conn):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.serve_status)
        assert result.exit_code == 0
        assert controller_utils.Controllers.SKY_SERVE_CONTROLLER.value.default_hint_if_non_existent in str(
            result.output), (result.exception, result.output, result.exc_info)
