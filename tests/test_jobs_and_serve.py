import tempfile
import textwrap

import click
from click import testing as cli_testing
import pytest
import yaml

import sky
from sky import backends
from sky import cli
from sky import exceptions
from sky import global_user_state
from sky.utils import common
from sky.utils import controller_utils
from sky.utils import db_utils


def test_job_nonexist_strategy():
    """Test the nonexist recovery strategy.

    This function is testing for the core functions on server side.
    """
    task_yaml = textwrap.dedent("""\
        resources:
            cloud: aws
            use_spot: true
            job_recovery: nonexist""")
    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(task_yaml)
        f.flush()
        with pytest.raises(ValueError,
                           match='is not a valid jobs recovery strategy among'):
            task = sky.Task.from_yaml(f.name)
            task.validate()


@pytest.fixture
def _mock_db_conn(tmp_path, monkeypatch):
    # Create a temporary database file
    db_path = tmp_path / 'state_testing.db'

    # Create a new SQLiteConn instance
    db_conn = db_utils.SQLiteConn(str(db_path), global_user_state.create_table)

    # Monkeypatch the global database connection
    monkeypatch.setattr(global_user_state, '_DB', db_conn)


def _generate_tmp_yaml(tmp_path, filename: str) -> str:
    yaml_path = tmp_path / filename
    private_key_path = tmp_path / 'id_rsa'
    private_key_path.write_text('test')
    text = yaml.dump({
        'auth': {
            'ssh_user': 'ubuntu',
            'ssh_private_key': str(private_key_path)
        },
        'provider': {
            'module': 'sky.backends.aws'
        }
    })
    yaml_path.write_text(text)
    return str(yaml_path)


@pytest.fixture
def _mock_cluster_state(_mock_db_conn, tmp_path):
    assert 'state.db' not in global_user_state._DB.db_path
    # Mock an empty /tmp/cluster1.yaml using tmp_path

    handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster1',
        cluster_name_on_cloud='test-cluster1',
        cluster_yaml=_generate_tmp_yaml(tmp_path, 'cluster1.yaml'),
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
        cluster_yaml=_generate_tmp_yaml(tmp_path, 'cluster2.yaml'),
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
        cluster_yaml=_generate_tmp_yaml(tmp_path, 'cluster3.yaml'),
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
def _mock_jobs_controller(_mock_db_conn, tmp_path):
    handle = backends.CloudVmRayResourceHandle(
        cluster_name=common.JOB_CONTROLLER_NAME,
        cluster_name_on_cloud=common.JOB_CONTROLLER_NAME,
        cluster_yaml=_generate_tmp_yaml(tmp_path, 'jobs_controller.yaml'),
        launched_nodes=1,
        launched_resources=sky.Resources(sky.AWS(),
                                         instance_type='m4.2xlarge',
                                         region='us-west-1'),
    )
    global_user_state.add_or_update_cluster(
        common.JOB_CONTROLLER_NAME,
        handle,
        requested_resources={handle.launched_resources},
        ready=True)


@pytest.fixture
def _mock_serve_controller(_mock_db_conn, tmp_path):
    yaml_path = _generate_tmp_yaml(tmp_path, 'serve_controller.yaml')
    handle = backends.CloudVmRayResourceHandle(
        cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
        cluster_name_on_cloud=common.SKY_SERVE_CONTROLLER_NAME,
        cluster_yaml=yaml_path,
        launched_nodes=1,
        launched_resources=sky.Resources(sky.AWS(),
                                         instance_type='m4.2xlarge',
                                         region='us-west-1'),
        stable_internal_external_ips=[('1.2.3.4', '4.3.2.1')],
        stable_ssh_ports=[22],
    )
    global_user_state.add_or_update_cluster(
        common.SKY_SERVE_CONTROLLER_NAME,
        handle,
        requested_resources={handle.launched_resources},
        ready=True)


class TestWithEmptyDBSetup:

    @pytest.fixture(autouse=True)
    def setup(self, _mock_db_conn, mock_client_requests):
        pass

    def test_cancel_jobs(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.jobs_cancel, ['-a', '-y'])
        assert result.exit_code == 1

        assert isinstance(result.exception, exceptions.ClusterNotUpError)
        assert controller_utils.Controllers.JOBS_CONTROLLER.value.default_hint_if_non_existent in str(
            result.exception), (result.exception, result.output,
                                result.exc_info)

    def test_logs_jobs(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.jobs_logs, ['1'])
        assert result.exit_code == 1
        assert controller_utils.Controllers.JOBS_CONTROLLER.value.default_hint_if_non_existent in str(
            result.exception), (result.exception, result.output,
                                result.exc_info)

    def test_queue_jobs(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.jobs_queue)
        assert result.exit_code == 0
        assert controller_utils.Controllers.JOBS_CONTROLLER.value.default_hint_if_non_existent in str(
            result.output), (result.exception, result.output, result.exc_info)

    @pytest.mark.timeout(60)
    def test_down_serve(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.serve_down, ['-a', '-y'])
        assert result.exit_code == 1

        assert isinstance(result.exception, exceptions.ClusterNotUpError)
        assert (controller_utils.Controllers.SKY_SERVE_CONTROLLER.value.
                default_hint_if_non_existent
                in str(result.exception)), (result.exception, result.output,
                                            result.exc_info)

    def test_logs_serve(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.serve_logs, ['test', '--controller'])
        assert controller_utils.Controllers.SKY_SERVE_CONTROLLER.value.default_hint_if_non_existent in str(
            result.exception), (result.exception, result.output,
                                result.exc_info)

    def test_status_serve(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.serve_status)
        assert result.exit_code == 0
        assert controller_utils.Controllers.SKY_SERVE_CONTROLLER.value.default_hint_if_non_existent in str(
            result.output), (result.exception, result.output, result.exc_info)


class TestJobsOperations:
    """Test operations for managed jobs."""

    @pytest.fixture(autouse=True)
    def setup(self, _mock_db_conn, _mock_cluster_state, _mock_jobs_controller,
              mock_controller_accessible, mock_job_table_one_job,
              mock_client_requests):
        pass

    def test_stop_jobs_controller(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.stop, [common.JOB_CONTROLLER_NAME])
        assert result.exit_code == click.UsageError.exit_code
        assert (f'Stopping controller(s) \'{common.JOB_CONTROLLER_NAME}\' is '
                'currently not supported' in result.output)

        result = cli_runner.invoke(cli.stop, ['sky-jobs-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.stop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    def test_autostop_jobs_controller(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.autostop, [common.JOB_CONTROLLER_NAME])
        assert result.exit_code == click.UsageError.exit_code
        assert ('Scheduling autostop on controller(s) '
                f'\'{common.JOB_CONTROLLER_NAME}\' is currently not supported'
                in result.output)

        result = cli_runner.invoke(cli.autostop, ['sky-jobs-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.autostop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    def test_cancel_on_jobs_controller(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.cancel,
                                   [common.JOB_CONTROLLER_NAME, '-a'])
        assert result.exit_code == click.UsageError.exit_code
        assert 'Cancelling the jobs controller\'s jobs is not allowed.' in str(
            result.output)

    def test_down_jobs_controller_no_job(self, mock_job_table_no_job,
                                         mock_client_requests):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.down, [common.JOB_CONTROLLER_NAME],
                                   input='n')
        assert 'WARNING: Tearing down the managed jobs controller.' in result.output, (
            result.exception, result.output, result.exc_info)
        assert isinstance(result.exception,
                          SystemExit), (result.exception, result.output)

    def test_down_jobs_controller_one_job(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.down, [common.JOB_CONTROLLER_NAME],
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


class TestServeOperations:
    """Test operations for services."""

    @pytest.fixture(autouse=True)
    def setup(self, _mock_db_conn, _mock_cluster_state, _mock_serve_controller,
              mock_client_requests):
        pass

    def test_stop_serve_controller(self,):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.stop, [common.SKY_SERVE_CONTROLLER_NAME])
        assert result.exit_code == click.UsageError.exit_code
        assert (
            f'Stopping controller(s) \'{common.SKY_SERVE_CONTROLLER_NAME}\' is '
            'currently not supported' in result.output)

        result = cli_runner.invoke(cli.stop, ['sky-serve-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.stop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    def test_autostop_serve_controller(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.autostop,
                                   [common.SKY_SERVE_CONTROLLER_NAME])
        assert result.exit_code == click.UsageError.exit_code
        assert (
            'Scheduling autostop on controller(s) '
            f'\'{common.SKY_SERVE_CONTROLLER_NAME}\' is currently not supported'
            in result.output)

        result = cli_runner.invoke(cli.autostop, ['sky-serve-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.autostop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    def test_cancel_on_serve_controller(self):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.cancel,
                                   [common.SKY_SERVE_CONTROLLER_NAME, '-a'])
        assert result.exit_code == click.UsageError.exit_code
        assert 'Cancelling the sky serve controller\'s jobs is not allowed.' in str(
            result.output)

    def test_down_serve_controller_one_service(self, mock_controller_accessible,
                                               mock_services_one_service):
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(cli.down, [common.SKY_SERVE_CONTROLLER_NAME],
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

    def test_down_serve_controller_no_service(self, mock_controller_accessible,
                                              mock_services_no_service):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.down, [common.SKY_SERVE_CONTROLLER_NAME],
                                   input='n')
        assert 'Terminate sky serve controller:' in result.output, (
            result.exception, result.output, result.exc_info)
        assert isinstance(result.exception,
                          SystemExit), (result.exception, result.output)
