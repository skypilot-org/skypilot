import pytest

import apex
from apex import backends
from apex import exceptions
from apex import global_user_state
from apex.utils import db_utils
from apex.utils import resources_utils


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
            launched_resources=apex.Resources(apex.AWS(),
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
            launched_resources=apex.Resources(apex.GCP(),
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
            launched_resources=apex.Resources(apex.Azure(),
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
            launched_resources=apex.Resources(
                apex.AWS(),
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
            launched_resources=apex.Resources(
                apex.GCP(),
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
        task = apex.Task(run='echo hi')
        task.set_resources(apex.Resources(accelerators='A100:8'))
        apex.launch(task, cluster_name='test-cluster1', dryrun=True)
        apex.exec(task, cluster_name='test-cluster1', dryrun=True)
        task.set_resources(apex.Resources(accelerators='A100:3'))
        apex.launch(task, cluster_name='test-cluster1', dryrun=True)
        apex.exec(task, cluster_name='test-cluster1', dryrun=True)
        task.set_resources(
            apex.Resources(
                apex.AWS(),
                accelerators='A100:1',
                region='us-east-1',
            ))
        apex.launch(task, cluster_name='test-cluster1', dryrun=True)
        apex.exec(task, cluster_name='test-cluster1', dryrun=True)

        task = apex.Task(run='echo hi')
        task.set_resources(apex.Resources(accelerators='V100:4'))
        apex.launch(task, cluster_name='test-cluster2', dryrun=True)
        apex.exec(task, cluster_name='test-cluster2', dryrun=True)
        task.set_resources(
            apex.Resources(apex.GCP(), accelerators='V100:3', region='us-west1'))
        apex.launch(task, cluster_name='test-cluster2', dryrun=True)
        apex.exec(task, cluster_name='test-cluster2', dryrun=True)

        task = apex.Task(run='echo hi')
        apex.exec(task, cluster_name='test-cluster3', dryrun=True)

        task = apex.Task(run='echo hi')
        task.set_resources(
            apex.Resources(disk_tier=resources_utils.DiskTier.BEST))
        apex.launch(task, cluster_name='test-disk-tier1', dryrun=True)
        apex.exec(task, cluster_name='test-disk-tier1', dryrun=True)
        apex.launch(task, cluster_name='test-disk-tier2', dryrun=True)
        apex.exec(task, cluster_name='test-disk-tier2', dryrun=True)
        task.set_resources(
            apex.Resources(disk_tier=resources_utils.DiskTier.LOW))
        apex.launch(task, cluster_name='test-disk-tier1', dryrun=True)
        apex.exec(task, cluster_name='test-disk-tier1', dryrun=True)
        apex.launch(task, cluster_name='test-disk-tier2', dryrun=True)
        apex.exec(task, cluster_name='test-disk-tier2', dryrun=True)
        task.set_resources(
            apex.Resources(disk_tier=resources_utils.DiskTier.HIGH))
        apex.launch(task, cluster_name='test-disk-tier1', dryrun=True)
        apex.exec(task, cluster_name='test-disk-tier1', dryrun=True)

    def _run_launch_exec_with_error(self, task, cluster_name):
        with pytest.raises(exceptions.ResourcesMismatchError) as e:
            apex.launch(task, cluster_name=cluster_name, dryrun=True)
            assert 'do not match the existing cluster.' in str(e.value), str(
                e.value)
        with pytest.raises(exceptions.ResourcesMismatchError) as e:
            apex.exec(task, cluster_name=cluster_name, dryrun=True)
            assert 'do not match the existing cluster.' in str(e.value), str(
                e.value)

    def test_launch_exec_mismatch(self, _mock_cluster_state, monkeypatch):
        """Test launch and exec on existing clusters with mismatched resources."""
        task = apex.Task(run='echo hi')
        # Accelerators mismatch
        task.set_resources(apex.Resources(accelerators='V100:8'))
        self._run_launch_exec_with_error(task, 'test-cluster1')
        self._run_launch_exec_with_error(task, 'test-cluster2')

        task.set_resources(apex.Resources(accelerators='A100:8'))
        self._run_launch_exec_with_error(task, 'test-cluster2')
        self._run_launch_exec_with_error(task, 'test-cluster3')

        # Cloud mismatch
        task.set_resources(apex.Resources(apex.AWS(), accelerators='V100'))
        self._run_launch_exec_with_error(task, 'test-cluster2')

        task.set_resources(apex.Resources(apex.GCP()))
        self._run_launch_exec_with_error(task, 'test-cluster1')

        # Disk tier mismatch
        task.set_resources(
            apex.Resources(disk_tier=resources_utils.DiskTier.HIGH))
        self._run_launch_exec_with_error(task, 'test-disk-tier2')
