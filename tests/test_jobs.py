import pytest

import sky
from sky import backends
from sky import exceptions
from sky import global_user_state
from sky.utils import db_utils
from sky.utils import resources_utils


class TestExecutionOnExistingClusters:
    """Test operations on existing clusters."""

    @pytest.fixture
    def _mock_db_conn(self, monkeypatch, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        db_path = tmp_path / 'state_testing_optimizer_dryrun.db'
        monkeypatch.setattr(
            global_user_state, '_DB',
            db_utils.SQLiteConn(str(db_path), global_user_state.create_table))

    @pytest.fixture
    def _mock_cluster_state(self, _mock_db_conn, enable_all_clouds):
        """Add clusters to the global state.

        This fixture adds five clusters to the global state:
        - test-cluster1: AWS, 2x p4d.24xlarge (8x A100)
        - test-cluster2: GCP, 1x n1-highmem-64, 4x V100
        - test-cluster3: Azure, 1x Standard_D4s_v3 (CPU only)
        - test-disk-tier1: AWS, 1x m6i.2xlarge, with best disk tier
        - test-disk-tier2: GCP, 1x n2-standard-8, with medium disk tier
        """
        assert 'state.db' not in global_user_state._DB.db_path

        handle = backends.CloudVmRayResourceHandle(
            cluster_name='test-cluster1',
            cluster_name_on_cloud='test-cluster1',
            cluster_yaml='/tmp/cluster1.yaml',
            launched_nodes=2,
            launched_resources=sky.Resources(sky.AWS(),
                                             instance_type='p4d.24xlarge',
                                             region='us-east-1',
                                             zone='us-east-1a'),
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
                                             instance_type='n1-highmem-64',
                                             accelerators='V100:4',
                                             region='us-west1',
                                             zone='us-west1-a'),
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
            cluster_name='test-disk-tier1',
            cluster_name_on_cloud='test-disk-tier1',
            cluster_yaml='/tmp/disk-tier1.yaml',
            launched_nodes=1,
            launched_resources=sky.Resources(
                sky.AWS(),
                instance_type='m6i.2xlarge',
                region='us-east-1',
                zone='us-east-1a',
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
                sky.GCP(),
                instance_type='n2-standard-8',
                region='us-west1',
                zone='us-west1-a',
                disk_tier=resources_utils.DiskTier.MEDIUM))
        global_user_state.add_or_update_cluster(
            'test-disk-tier2',
            handle,
            requested_resources={handle.launched_resources},
            ready=True)

    def test_launch_exec(self, _mock_cluster_state, monkeypatch):
        """Test launch and exec on existing clusters.

        This test runs launch and exec with less demanding resources
        than the existing clusters can pass the check.
        """
        task = sky.Task(run='echo hi')
        task.set_resources(sky.Resources(accelerators='A100:8'))
        sky.launch(task, cluster_name='test-cluster1', dryrun=True)
        sky.exec(task, cluster_name='test-cluster1', dryrun=True)
        task.set_resources(sky.Resources(accelerators='A100:3'))
        sky.launch(task, cluster_name='test-cluster1', dryrun=True)
        sky.exec(task, cluster_name='test-cluster1', dryrun=True)
        task.set_resources(
            sky.Resources(
                sky.AWS(),
                accelerators='A100:1',
                region='us-east-1',
            ))
        sky.launch(task, cluster_name='test-cluster1', dryrun=True)
        sky.exec(task, cluster_name='test-cluster1', dryrun=True)

        task = sky.Task(run='echo hi')
        task.set_resources(sky.Resources(accelerators='V100:4'))
        sky.launch(task, cluster_name='test-cluster2', dryrun=True)
        sky.exec(task, cluster_name='test-cluster2', dryrun=True)
        task.set_resources(
            sky.Resources(sky.GCP(), accelerators='V100:3', region='us-west1'))
        sky.launch(task, cluster_name='test-cluster2', dryrun=True)
        sky.exec(task, cluster_name='test-cluster2', dryrun=True)

        task = sky.Task(run='echo hi')
        sky.exec(task, cluster_name='test-cluster3', dryrun=True)

        task = sky.Task(run='echo hi')
        task.set_resources(
            sky.Resources(disk_tier=resources_utils.DiskTier.BEST))
        sky.launch(task, cluster_name='test-disk-tier1', dryrun=True)
        sky.exec(task, cluster_name='test-disk-tier1', dryrun=True)
        sky.launch(task, cluster_name='test-disk-tier2', dryrun=True)
        sky.exec(task, cluster_name='test-disk-tier2', dryrun=True)
        task.set_resources(
            sky.Resources(disk_tier=resources_utils.DiskTier.LOW))
        sky.launch(task, cluster_name='test-disk-tier1', dryrun=True)
        sky.exec(task, cluster_name='test-disk-tier1', dryrun=True)
        sky.launch(task, cluster_name='test-disk-tier2', dryrun=True)
        sky.exec(task, cluster_name='test-disk-tier2', dryrun=True)
        task.set_resources(
            sky.Resources(disk_tier=resources_utils.DiskTier.HIGH))
        sky.launch(task, cluster_name='test-disk-tier1', dryrun=True)
        sky.exec(task, cluster_name='test-disk-tier1', dryrun=True)

    def _run_launch_exec_with_error(self, task, cluster_name):
        with pytest.raises(exceptions.ResourcesMismatchError) as e:
            sky.launch(task, cluster_name=cluster_name, dryrun=True)
            assert 'do not match the existing cluster.' in str(e.value), str(
                e.value)
        with pytest.raises(exceptions.ResourcesMismatchError) as e:
            sky.exec(task, cluster_name=cluster_name, dryrun=True)
            assert 'do not match the existing cluster.' in str(e.value), str(
                e.value)

    def test_launch_exec_mismatch(self, _mock_cluster_state, monkeypatch):
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
        task.set_resources(sky.Resources(sky.AWS(), accelerators='V100'))
        self._run_launch_exec_with_error(task, 'test-cluster2')

        task.set_resources(sky.Resources(sky.GCP()))
        self._run_launch_exec_with_error(task, 'test-cluster1')

        # Disk tier mismatch
        task.set_resources(
            sky.Resources(disk_tier=resources_utils.DiskTier.HIGH))
        self._run_launch_exec_with_error(task, 'test-disk-tier2')
