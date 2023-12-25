import tempfile
import textwrap

import click
from click import testing as cli_testing
import pytest

import sky
from sky import backends
from sky import cli
from sky import global_user_state
from sky import spot
from sky.utils import db_utils


def test_spot_nonexist_strategy():
    """Test the nonexist recovery strategy."""
    task_yaml = textwrap.dedent("""\
        resources:
            cloud: aws
            use_spot: true
            spot_recovery: nonexist""")
    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(task_yaml)
        f.flush()
        with pytest.raises(
                ValueError,
                match='is not supported. The strategy should be among'):
            sky.Task.from_yaml(f.name)


class TestControllerOperations:
    """Test operations on controllers."""

    @pytest.fixture
    def _mock_db_conn(self, monkeypatch, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        db_path = tmp_path / 'state_testing.db'
        monkeypatch.setattr(
            global_user_state, '_DB',
            db_utils.SQLiteConn(str(db_path), global_user_state.create_table))

    @pytest.fixture
    def _mock_cluster_state(self, _mock_db_conn):
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
        handle = backends.CloudVmRayResourceHandle(
            cluster_name=spot.SPOT_CONTROLLER_NAME,
            cluster_name_on_cloud=spot.SPOT_CONTROLLER_NAME,
            cluster_yaml='/tmp/spot_controller.yaml',
            launched_nodes=1,
            launched_resources=sky.Resources(sky.AWS(),
                                             instance_type='m4.2xlarge',
                                             region='us-west-1'),
        )
        global_user_state.add_or_update_cluster(
            spot.SPOT_CONTROLLER_NAME,
            handle,
            requested_resources={handle.launched_resources},
            ready=True)

    @pytest.mark.timeout(60)
    def test_down_spot_controller(self, _mock_cluster_state, monkeypatch):

        def mock_cluster_refresh_up(
            cluster_name: str,
            *,
            force_refresh_statuses: bool = False,
            acquire_per_cluster_status_lock: bool = True,
        ):
            record = global_user_state.get_cluster_from_name(cluster_name)
            return record['status'], record['handle']

        monkeypatch.setattr(
            'sky.backends.backend_utils.refresh_cluster_status_handle',
            mock_cluster_refresh_up)

        monkeypatch.setattr('sky.core.spot_queue', lambda refresh: [])

        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.down, [spot.SPOT_CONTROLLER_NAME],
                                   input='n')
        assert 'WARNING: Tearing down the managed spot controller (UP).' in result.output
        assert isinstance(result.exception,
                          SystemExit), (result.exception, result.output)

        result = cli_runner.invoke(cli.down, ['sky-spot-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.down, ['sky-spot-con*', '-p'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.down, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit), result.exception
        assert 'Aborted' in result.output

        result = cli_runner.invoke(cli.down, ['-ap'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    @pytest.mark.timeout(60)
    def test_stop_spot_controller(self, _mock_cluster_state):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.stop, [spot.SPOT_CONTROLLER_NAME])
        assert result.exit_code == click.UsageError.exit_code
        assert (f'Stopping controller(s) \'{spot.SPOT_CONTROLLER_NAME}\' is '
                'currently not supported' in result.output)

        result = cli_runner.invoke(cli.stop, ['sky-spot-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.stop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    @pytest.mark.timeout(60)
    def test_autostop_spot_controller(self, _mock_cluster_state):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.autostop, [spot.SPOT_CONTROLLER_NAME])
        assert result.exit_code == click.UsageError.exit_code
        assert ('Scheduling autostop on controller(s) '
                f'\'{spot.SPOT_CONTROLLER_NAME}\' is currently not supported'
                in result.output)

        result = cli_runner.invoke(cli.autostop, ['sky-spot-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.autostop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    def test_cancel_on_spot_controller(self, _mock_cluster_state):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.cancel,
                                   [spot.SPOT_CONTROLLER_NAME, '-a'])
        assert result.exit_code == 1
        assert 'Cancelling the spot controller\'s jobs is not allowed.' in str(
            result.output)
