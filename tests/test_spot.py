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


class TestReservedClustersOperations:
    """Test operations on reserved clusters."""

    @pytest.fixture
    def _mock_db_conn(self, monkeypatch, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        db_path = tmp_path / 'state_testing.db'
        monkeypatch.setattr(global_user_state, '_DB',
                            global_user_state._SQLiteConn(str(db_path)))

    @pytest.fixture
    def _mock_cluster_state(self, _mock_db_conn):
        assert 'state.db' not in global_user_state._DB.db_path
        handle = backends.CloudVmRayBackend.ResourceHandle(
            cluster_name='test-cluster1',
            cluster_yaml='/tmp/cluster1.yaml',
            head_ip='1.1.1.1',
            launched_nodes=2,
            launched_resources=sky.Resources(sky.AWS(),
                                             instance_type='p3.2xlarge',
                                             region='us-east-1'),
        )
        global_user_state.add_or_update_cluster('test-cluster1',
                                                handle,
                                                ready=True)
        handle = backends.CloudVmRayBackend.ResourceHandle(
            cluster_name='test-cluster2',
            cluster_yaml='/tmp/cluster2.yaml',
            head_ip='1.1.1.2',
            launched_nodes=1,
            launched_resources=sky.Resources(sky.GCP(),
                                             instance_type='n1-highmem-8',
                                             accelerators={'A100': 4},
                                             region='us-west1'),
        )
        global_user_state.add_or_update_cluster('test-cluster2',
                                                handle,
                                                ready=True)
        handle = backends.CloudVmRayBackend.ResourceHandle(
            cluster_name='test-cluster3',
            cluster_yaml='/tmp/cluster3.yaml',
            head_ip='1.1.1.3',
            launched_nodes=4,
            launched_resources=sky.Resources(sky.Azure(),
                                             instance_type='Standard_D4s_v3',
                                             region='eastus'),
        )
        global_user_state.add_or_update_cluster('test-cluster3',
                                                handle,
                                                ready=False)
        handle = backends.CloudVmRayBackend.ResourceHandle(
            cluster_name=spot.SPOT_CONTROLLER_NAME,
            cluster_yaml='/tmp/spot_controller.yaml',
            head_ip='1.1.1.4',
            launched_nodes=1,
            launched_resources=sky.Resources(sky.AWS(),
                                             instance_type='m4.2xlarge',
                                             region='us-west-1'),
        )
        global_user_state.add_or_update_cluster(spot.SPOT_CONTROLLER_NAME,
                                                handle,
                                                ready=True)

    @pytest.mark.timeout(60)
    def test_down_spot_controller(self, _mock_cluster_state):
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(cli.down, ['sky-spot-controller'])
        assert result.exit_code == click.UsageError.exit_code
        assert ('Terminating Sky reserved cluster(s) \'sky-spot-controller\' '
                'is not supported' in result.output)

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
        result = cli_runner.invoke(cli.stop, ['sky-spot-controller'])
        assert result.exit_code == click.UsageError.exit_code
        assert ('Stopping Sky reserved cluster(s) \'sky-spot-controller\' is '
                'not supported' in result.output)

        result = cli_runner.invoke(cli.stop, ['sky-spot-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.stop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    @pytest.mark.timeout(60)
    def test_autostop_spot_controller(self, _mock_cluster_state):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.autostop, ['sky-spot-controller'])
        assert result.exit_code == click.UsageError.exit_code
        assert ('Scheduling auto-stop on Sky reserved cluster(s) '
                '\'sky-spot-controller\' is not supported' in result.output)

        result = cli_runner.invoke(cli.autostop, ['sky-spot-con*'])
        assert not result.exception
        assert 'Cluster(s) not found' in result.output

        result = cli_runner.invoke(cli.autostop, ['-a'], input='n\n')
        assert isinstance(result.exception, SystemExit)
        assert 'Aborted' in result.output

    def test_cancel_on_spot_controller(self, _mock_cluster_state):
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(cli.cancel, ['sky-spot-controller', '-a'])
        assert isinstance(result.exception, ValueError)
        assert 'Cancelling jobs is not allowed' in str(result.exception)
