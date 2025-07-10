import pytest
from sqlalchemy import create_engine

import sky
from sky import backends
from sky import exceptions
from sky import global_user_state
from sky.utils import db_utils
from sky.utils import resources_utils


@pytest.fixture
def _mock_db_conn(tmp_path, monkeypatch):
    # Create a temporary database file
    db_path = tmp_path / 'state_testing.db'

    sqlalchemy_engine = create_engine(f'sqlite:///{db_path}')

    monkeypatch.setattr(global_user_state, '_SQLALCHEMY_ENGINE',
                        sqlalchemy_engine)

    global_user_state.create_table(sqlalchemy_engine)


@pytest.fixture
def _mock_cluster_state(_mock_db_conn, enable_all_clouds):
    """Add clusters to the global state.

    This fixture adds five clusters to the global state:
    - test-cluster1: AWS, 2x p4d.24xlarge (8x A100)
    - test-cluster2: GCP, 1x n1-highmem-64, 4x V100
    - test-cluster3: Azure, 1x Standard_D4s_v3 (CPU only)
    - test-disk-tier1: AWS, 1x m6i.2xlarge, with best disk tier
    - test-disk-tier2: GCP, 1x n2-standard-8, with medium disk tier
    """
    assert 'state.db' not in global_user_state._SQLALCHEMY_ENGINE.url

    handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster1',
        cluster_name_on_cloud='test-cluster1',
        cluster_yaml='/tmp/cluster1.yaml',
        launched_nodes=2,
        launched_resources=sky.Resources(infra='aws/us-east-1/us-east-1a',
                                         instance_type='p4d.24xlarge'),
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
        launched_resources=sky.Resources(infra='gcp/us-west1/us-west1-a',
                                         instance_type='n1-highmem-64',
                                         accelerators='V100:4'),
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
        launched_nodes=1,
        launched_resources=sky.Resources(infra='azure/eastus',
                                         instance_type='Standard_D4s_v3'),
    )
    global_user_state.add_or_update_cluster(
        'test-cluster3',
        handle,
        requested_resources={handle.launched_resources},
        ready=False)
    handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-disk-tier1',
        cluster_name_on_cloud='test-disk-tier1',
        cluster_yaml='/tmp/disk-tier1.yaml',
        launched_nodes=1,
        launched_resources=sky.Resources(
            infra='aws/us-east-1/us-east-1a',
            instance_type='m6i.2xlarge',
            disk_tier=resources_utils.DiskTier.BEST))
    global_user_state.add_or_update_cluster(
        'test-disk-tier1',
        handle,
        requested_resources={handle.launched_resources},
        ready=True)
    handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-disk-tier2',
        cluster_name_on_cloud='test-disk-tier2',
        cluster_yaml='/tmp/disk-tier2.yaml',
        launched_nodes=1,
        launched_resources=sky.Resources(
            infra='gcp/us-west1/us-west1-a',
            instance_type='n2-standard-8',
            disk_tier=resources_utils.DiskTier.MEDIUM))
    global_user_state.add_or_update_cluster(
        'test-disk-tier2',
        handle,
        requested_resources={handle.launched_resources},
        ready=True)


class TestExecutionOnExistingClusters:
    """Test operations on existing clusters."""

    @pytest.fixture(autouse=True)
    def setup(self, _mock_cluster_state):
        # Use setup fixture to avoid passing _mock_cluster_state to each test
        pass

    def _run_launch_exec_with_error(self, task, cluster_name):
        with pytest.raises(exceptions.ResourcesMismatchError) as e:
            sky.stream_and_get(
                sky.launch(task, cluster_name=cluster_name, dryrun=True))
        assert 'do not match the existing cluster.' in str(e.value), str(
            e.value)
        with pytest.raises(exceptions.ResourcesMismatchError) as e:
            sky.stream_and_get(
                sky.exec(task, cluster_name=cluster_name, dryrun=True))
        assert 'do not match the existing cluster.' in str(e.value), str(
            e.value)

    def test_launch_exec(self):
        """Test launch and exec on existing clusters.
        This test runs launch and exec with less demanding resources
        than the existing clusters can pass the check.
        """
        task = sky.Task(run='echo hi')
        task.set_resources(sky.Resources(accelerators='A100:8'))
        sky.stream_and_get(
            sky.launch(task, cluster_name='test-cluster1', dryrun=True))
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-cluster1', dryrun=True))
        task.set_resources(sky.Resources(accelerators='A100:3'))
        sky.stream_and_get(
            sky.launch(task, cluster_name='test-cluster1', dryrun=True))
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-cluster1', dryrun=True))
        task.set_resources(
            sky.Resources(
                infra='aws/us-east-1',
                accelerators='A100:1',
            ))
        sky.stream_and_get(
            sky.launch(task, cluster_name='test-cluster1', dryrun=True))
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-cluster1', dryrun=True))

        task = sky.Task(run='echo hi')
        task.set_resources(sky.Resources(accelerators='V100:4'))
        sky.stream_and_get(
            sky.launch(task, cluster_name='test-cluster2', dryrun=True))
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-cluster2', dryrun=True))
        task.set_resources(
            sky.Resources(infra='gcp/us-west1', accelerators='V100:3'))
        sky.stream_and_get(
            sky.launch(task, cluster_name='test-cluster2', dryrun=True))
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-cluster2', dryrun=True))

        task = sky.Task(run='echo hi')
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-cluster3', dryrun=True))

        task = sky.Task(run='echo hi')
        task.set_resources(
            sky.Resources(disk_tier=resources_utils.DiskTier.BEST))
        sky.stream_and_get(
            sky.launch(task, cluster_name='test-disk-tier1', dryrun=True))
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-disk-tier1', dryrun=True))
        sky.stream_and_get(
            sky.launch(task, cluster_name='test-disk-tier2', dryrun=True))
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-disk-tier2', dryrun=True))
        task.set_resources(
            sky.Resources(disk_tier=resources_utils.DiskTier.LOW))
        sky.stream_and_get(
            sky.launch(task, cluster_name='test-disk-tier1', dryrun=True))
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-disk-tier1', dryrun=True))
        sky.stream_and_get(
            sky.launch(task, cluster_name='test-disk-tier2', dryrun=True))
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-disk-tier2', dryrun=True))
        task.set_resources(
            sky.Resources(disk_tier=resources_utils.DiskTier.HIGH))
        sky.stream_and_get(
            sky.launch(task, cluster_name='test-disk-tier1', dryrun=True))
        sky.stream_and_get(
            sky.exec(task, cluster_name='test-disk-tier1', dryrun=True))

    def test_launch_exec_mismatch(self):
        """Test launch and exec on existing clusters with mismatched resources."""
        task = sky.Task(run='echo hi')
        # Accelerators mismatch
        task.set_resources(sky.Resources(accelerators='V100:8'))
        self._run_launch_exec_with_error(task, 'test-cluster1')
        self._run_launch_exec_with_error(task, 'test-cluster2')

        task.set_resources(sky.Resources(accelerators='A100:8'))
        self._run_launch_exec_with_error(task, 'test-cluster2')
        self._run_launch_exec_with_error(task, 'test-cluster3')

        # Cloud mismatch
        task.set_resources(sky.Resources(infra='aws', accelerators='V100'))
        self._run_launch_exec_with_error(task, 'test-cluster2')

        task.set_resources(sky.Resources(infra='gcp'))
        self._run_launch_exec_with_error(task, 'test-cluster1')

        # Disk tier mismatch
        task.set_resources(
            sky.Resources(disk_tier=resources_utils.DiskTier.HIGH))
        self._run_launch_exec_with_error(task, 'test-disk-tier2')
