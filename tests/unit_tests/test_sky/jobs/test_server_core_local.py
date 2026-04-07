"""Unit tests for local consolidation-mode managed jobs server core paths."""

import shutil
import types

import sky
from sky import backends
from sky import clouds
from sky.jobs.server import core as jobs_core


def _make_local_handle() -> backends.LocalResourcesHandle:
    cluster_name = 'local-test-consolidation'
    return backends.LocalResourcesHandle(
        cluster_name=cluster_name,
        cluster_name_on_cloud=cluster_name,
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=sky.Resources(cloud=clouds.Cloud(),
                                         instance_type=cluster_name),
    )


def test_consolidated_launch_uses_direct_scheduler_submit(
        monkeypatch, tmp_path):
    local_handle = _make_local_handle()
    submit_calls = {}

    remote_user_yaml_path = str(tmp_path / 'job.yaml')
    remote_original_user_yaml_path = str(tmp_path / 'job.original.yaml')
    remote_env_file_path = str(tmp_path / 'job.env')

    local_user_yaml_path = tmp_path / 'local-job.yaml'
    local_user_yaml_path.write_text('name: test-job\n', encoding='utf-8')
    local_original_user_yaml_path = tmp_path / 'local-job.original.yaml'
    local_original_user_yaml_path.write_text('name: test-job\n',
                                             encoding='utf-8')

    class FakeCloudVmRayBackend:
        pass

    class FakeBackend(FakeCloudVmRayBackend):

        def sync_file_mounts(self, handle, all_file_mounts, storage_mounts):
            assert handle is local_handle
            assert storage_mounts == {}
            for target, source in all_file_mounts.items():
                shutil.copyfile(source, target)

        def run_on_head(self, *args, **kwargs):
            raise AssertionError('run_on_head() should not be used locally')

    def fake_submit_jobs(**kwargs):
        submit_calls.update(kwargs)

    monkeypatch.setattr(jobs_core.backend_utils, 'is_controller_accessible',
                        lambda **kwargs: local_handle)
    monkeypatch.setattr(jobs_core.backends, 'CloudVmRayBackend',
                        FakeCloudVmRayBackend)
    monkeypatch.setattr(jobs_core.backend_utils, 'get_backend_from_handle',
                        lambda handle: FakeBackend())
    monkeypatch.setattr(jobs_core.managed_job_scheduler, 'submit_jobs',
                        fake_submit_jobs)

    controller_task = types.SimpleNamespace(
        file_mounts={
            remote_user_yaml_path: str(local_user_yaml_path),
            remote_original_user_yaml_path: str(local_original_user_yaml_path),
        },
        storage_mounts={},
    )

    job_ids, handle = jobs_core._consolidated_launch(
        controller=jobs_core.controller_utils.Controllers.JOBS_CONTROLLER,
        controller_task=controller_task,
        job_ids=[3, 5],
        remote_user_yaml_path=remote_user_yaml_path,
        remote_original_user_yaml_path=remote_original_user_yaml_path,
        remote_env_file_path=remote_env_file_path,
        controller_envs={
            'SKYPILOT_USER_ID': 'user-123',
            'SKYPILOT_TEST_FLAG': 'true',
        },
        priority=7,
        priority_class='high',
        job_id_to_rank={
            '3': 0,
            '5': 1,
        },
    )

    assert job_ids == [3, 5]
    assert handle is local_handle
    assert submit_calls == {
        'job_ids': [3, 5],
        'dag_yaml_path': remote_user_yaml_path,
        'original_user_yaml_path': remote_original_user_yaml_path,
        'env_file_path': remote_env_file_path,
        'priority': 7,
        'priority_class': 'high',
    }
    env_file_contents = (tmp_path / 'job.env').read_text(encoding='utf-8')
    assert 'export SKYPILOT_USER_ID=' in env_file_contents
    assert 'user-123' in env_file_contents
    assert 'export SKYPILOT_TEST_FLAG=true' in env_file_contents
    assert 'export SKYPILOT_JOB_ID_TO_RANK=' in env_file_contents
    assert '{"3": 0, "5": 1}' in env_file_contents
