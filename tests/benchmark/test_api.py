import pytest
from smoke_tests.smoke_tests_utils import get_cluster_name, BenchmarkTest, run_one_benchmark, LOW_RESOURCE_ARG
from pytest_benchmark.fixture import BenchmarkFixture

import sky
# from sky import skypilot_config
# from sky.skylet import constants
# from sky.skylet import events
# from sky.utils import common_utils
# from sky.utils import yaml_utils
from typing import Optional, Tuple, Union, Dict, Any, List, Iterator, Set
from pathlib import Path
from sky import task as task_lib
from sky import resources
from sky.data import storage as storage_lib
from sky import resources as resources_lib
from sky.utils import resources_utils
from sky.clouds.cloud import CloudImplementationFeatures
from sky import execution
from sky import dag
from sky.client import sdk
import time
from itertools import chain

# def test_api_launch(benchmark: BenchmarkFixture, generic_cloud: str):
#     name = get_cluster_name()
#     test = BenchmarkTest(
#         'api_launch',
#         setup_command=None,
#         benchmarked_command=[f'sky launch -y -c {name} {LOW_RESOURCE_ARG} examples/minimal.yaml'],
#         teardown=f'sky down -y {name}',
#     )
#     run_one_benchmark(test, benchmark)

# def test_api_down(benchmark: BenchmarkFixture, generic_cloud: str):
#     name = get_cluster_name()
#     test = BenchmarkTest(
#         'api_down',
#         setup_command=f'sky launch -y -c {name} {LOW_RESOURCE_ARG} examples/minimal.yaml',
#         benchmarked_command=[f'sky down -y {name}'],
#         teardown=f'sky down -y {name}',
#     )
#     run_one_benchmark(test, benchmark)


SIMPLE_TASK = sky.Task(
    run='echo hello SkyPilot',
    resources=sky.Resources(cpus='2', memory='4GB'))

ROUNDS_PER_TEST = 1

def _wait_for_cluster_to_be_up(name: str):
    finished = False
    while not finished:
        try:
            req_id = sdk.status(cluster_names=[name])
            res = sdk.get(req_id)
            if res[0]['status'] == sky.ClusterStatus.UP:
                finished = True
                time.sleep(3)
            else:
                time.sleep(1)
        except Exception as e:
            print(e)
            time.sleep(1)

class TestBasicSDKAPI:
    def test_sdk_api_launch(self, benchmark: BenchmarkFixture, generic_cloud: str):
        name = get_cluster_name() + "-lloyd"
        simple_task = sky.Task(
            run='echo hello SkyPilot',
            resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
        )

        def down_cluster():
            req_id = sdk.down(name)
            try:
                res = sdk.get(req_id)
            except sky.exceptions.ClusterDoesNotExist as e:
                pass


        def submit_and_get_request_id():
            id = sdk.launch(task=simple_task, cluster_name=name)
            return sdk.get(id)

        benchmark.pedantic(
            submit_and_get_request_id,
            setup=down_cluster,
            iterations=1,
            rounds=ROUNDS_PER_TEST)
        down_cluster()

    def test_sdk_api_down(self, benchmark: BenchmarkFixture, generic_cloud: str):
        name = get_cluster_name() + "-lloyd"
        simple_task = sky.Task(
            run='echo hello SkyPilot',
            resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
        )

        def down_cluster_then_launch():
            req_id = sdk.down(name)
            try:
                sdk.get(req_id)
            except sky.exceptions.ClusterDoesNotExist as e:
                pass

            req_id = sdk.launch(task=simple_task, cluster_name=name)
            sdk.get(req_id)


        def submit_and_get_request_id():
            req_id = sdk.down(cluster_name=name)
            return sdk.get(req_id)

        benchmark.pedantic(
            submit_and_get_request_id,
            kwargs={},
            setup=down_cluster_then_launch,
            iterations=1,
            rounds=ROUNDS_PER_TEST)

    def test_sdk_api_tail_logs(self, benchmark: BenchmarkFixture, generic_cloud: str):
        name = get_cluster_name() + "-lloyd"
        simple_task = sky.Task(
            run='echo hello SkyPilot',
            resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
        )

        job_id = None

        def teardown():
            nonlocal job_id
            if job_id is not None:
                req_id = sdk.down(name)
                sdk.get(req_id)
                job_id = None

        def launch_cluster():
            nonlocal job_id
            teardown()
            req_id = sdk.launch(task=simple_task, cluster_name=name)
            job_id = sdk.get(req_id)[0]
            _wait_for_cluster_to_be_up(name)
        
        def tail_logs():
            nonlocal job_id
            try:
                req_id = sdk.tail_logs(cluster_name=name, job_id=job_id, follow=False)
                return sdk.get(req_id)
            except Exception as e:
                return str(e)

        res = benchmark.pedantic(
            tail_logs,
            kwargs={},
            setup=launch_cluster,
            iterations=1,
            rounds=ROUNDS_PER_TEST)
        teardown()
        assert "Job finished (status: SUCCEEDED)." in res

    def test_sdk_status(self, benchmark: BenchmarkFixture, generic_cloud: str):
        name = get_cluster_name() + "-lloyd"
        simple_task = sky.Task(
            run='echo hello SkyPilot',
            resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
        )

        def teardown():
            req_id = sdk.down(name)
            sdk.get(req_id)

        def launch_cluster():
            req_id = sdk.launch(task=simple_task, cluster_name=name)
            sdk.get(req_id)[0]
            _wait_for_cluster_to_be_up(name)

        def status():
            req_id = sdk.status(cluster_names=[name])
            return sdk.get(req_id)

        launch_cluster()
        res = benchmark.pedantic(
            status,
            iterations=1,
            rounds=ROUNDS_PER_TEST)
        assert res[0]['status'] == sky.ClusterStatus.UP
        assert res[0]['name'] == name
        teardown()

    def test_sdk_status_refresh(self, benchmark: BenchmarkFixture, generic_cloud: str):
        name = get_cluster_name() + "-lloyd"
        simple_task = sky.Task(
            run='echo hello SkyPilot',
            resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
        )

        def teardown():
            req_id = sdk.down(name)
            sdk.get(req_id)

        def launch_cluster():
            req_id = sdk.launch(task=simple_task, cluster_name=name)
            sdk.get(req_id)[0]
            _wait_for_cluster_to_be_up(name)

        def status():
            req_id = sdk.status(cluster_names=[name], refresh=sky.StatusRefreshMode.FORCE)
            return sdk.get(req_id)

        launch_cluster()
        res = benchmark.pedantic(
            status,
            iterations=1,
            rounds=ROUNDS_PER_TEST)
        assert res[0]['status'] == sky.ClusterStatus.UP
        assert res[0]['name'] == name
        teardown()

    def test_sdk_check(self, benchmark: BenchmarkFixture, generic_cloud: str):
        def check():
            req_id = sdk.check(infra_list=[generic_cloud], verbose=False)
            return sdk.get(req_id)

        res = benchmark.pedantic(
            check,
            iterations=1,
            rounds=ROUNDS_PER_TEST)
        enabled_clouds = list(chain.from_iterable(res.values()))
        enabled_clouds = [cloud.lower() for cloud in enabled_clouds]
        assert generic_cloud in enabled_clouds, f"{generic_cloud} not in enabled."

class FakeResourceHandle(sky.backends.ResourceHandle):
    def __init__(self, cluster_name: str):
        self.cluster_name = cluster_name

    def get_cluster_name(self) -> str:
        return self.cluster_name

class FakeBackend(sky.backends.Backend):
    def __init__(self, cluster_name: str,
                 provision_latency_s: float = 30,
                 setup_latency_s: float = 5,
                 sync_workdir_latency_s: float = 30,
                 add_storage_objects_latency_s: float = 5,
                 sync_file_mounts_latency_s: float = 5,
                 exec_code_on_head_latency_s: float = 5,
                 execute_latency_s: float = 5,
                 teardown_latency_s: float = 5):
        self.cluster_name = cluster_name
        self.provision_latency_s = provision_latency_s
        self.setup_latency_s = setup_latency_s
        self.sync_workdir_latency_s = sync_workdir_latency_s
        self.add_storage_objects_latency_s = add_storage_objects_latency_s
        self.sync_file_mounts_latency_s = sync_file_mounts_latency_s
        self.exec_code_on_head_latency_s = exec_code_on_head_latency_s
        self.execute_latency_s = execute_latency_s
        self.teardown_latency_s = teardown_latency_s
        self.job_id = 0
        self.resource_handle = FakeResourceHandle(cluster_name)

    def check_resource_fit_cluster(self, handle: sky.backends.ResourceHandle, task: sky.Task):
        return task.resources

    def _provision(
        self,
        task: task_lib.Task,
        to_provision: Optional['resources.Resources'],
        dryrun: bool,
        stream_logs: bool,
        cluster_name: str,
        retry_until_up: bool = False,
        skip_unnecessary_provisioning: bool = False,
    ) -> Tuple[Optional[FakeResourceHandle], bool]:
        time.sleep(self.provision_latency_s)
        return self.resource_handle, False
    
    def _sync_workdir(self, handle: FakeResourceHandle,
                      workdir: Union[Path, Dict[str, Any]],
                      envs_and_secrets: Dict[str, str]) -> None:
        time.sleep(self.sync_workdir_latency_s)
    
    def _sync_file_mounts(
        self,
        handle: FakeResourceHandle,
        all_file_mounts: Optional[Dict[Path, Path]],
        storage_mounts: Optional[Dict[Path, 'storage_lib.Storage']],
    ) -> None:
        time.sleep(self.sync_file_mounts_latency_s)

    def add_storage_objects(self, task: task_lib.Task) -> None:
        time.sleep(self.add_storage_objects_latency_s)

    def _setup(self, handle: FakeResourceHandle, task: task_lib.Task,
               detach_setup: bool) -> None:
        time.sleep(self.setup_latency_s)

    def _exec_code_on_head(
        self,
        handle: FakeResourceHandle,
        codegen: str,
        job_id: int,
        detach_run: bool = False,
        managed_job_dag: Optional[dag.Dag] = None,
        remote_log_dir: Optional[str] = None,
    ) -> None:
        time.sleep(self.exec_code_on_head_latency_s)

    def _execute(self,
                handle: FakeResourceHandle,
                task: task_lib.Task,
                detach_run: bool,
                dryrun: bool = False) -> Optional[int]:
        job_id = self.job_id
        self.job_id += 1
        time.sleep(self.execute_latency_s)
        return job_id
    
    def _post_execute(self, handle: FakeResourceHandle, down: bool) -> None:
        pass

    def _teardown_ephemeral_storage(self, task: task_lib.Task) -> None:
        pass
    
    def _teardown(self, handle: FakeResourceHandle, terminate: bool, purge: bool = False) -> None:
        time.sleep(self.teardown_latency_s)

class FakeCloud(sky.clouds.Cloud):
    def __init__(self, provision_latency_s: float = 3):
        self.name = "FakeCloud"
        self.provision_latency_s = provision_latency_s

    def regions_with_offering(self, instance_type: str, accelerators: Optional[Dict[str, int]], use_spot: bool, region: Optional[str], zone: Optional[str]) -> List[sky.clouds.Region]:
        return [sky.clouds.Region(name="FakeRegion")]

    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Optional[List[sky.clouds.Zone]]]:
        time.sleep(cls.provision_latency_s)
        yield [sky.clouds.Zone(name="FakeZone")]
    
    def check_features_are_supported(
        cls, resources: resources_lib.Resources,
        requested_features: Set[CloudImplementationFeatures]) -> None:
        pass

    def _get_feasible_launchable_resources(
        self, resources: resources_lib.Resources
    ) -> resources_utils.FeasibleResources:
        return resources_utils.FeasibleResources([resources], [], None)
    

class TestScaleSDKAPI:
    def test_sdk_scale_launch(self,
                                monkeypatch: pytest.MonkeyPatch,
                                benchmark: BenchmarkFixture,
                                generic_cloud: str,
                                num_clusters: int = 1,
                                check_capabilities_latency_s: float = 0):
        # monkeypatch.setattr(sky.backends, 'CloudVmRayBackend', FakeBackend)
        task = SIMPLE_TASK
        task.best_resources = task.resources
        cluster_name = f"cluster-1"

        # Monkeypatch this so that isinstance(backend, backends.CloudVmRayBackend) is True
        monkeypatch.setattr(sky.backends, 'CloudVmRayBackend', FakeBackend)

        def check_capabilities(*args, **kwargs):
            time.sleep(check_capabilities_latency_s)
            return {"default": {"FakeCloud": ["compute"]}}
        monkeypatch.setattr(sky.check, 'check_capabilities', check_capabilities)

        monkeypatch.setattr(sky.check, 'get_cached_enabled_clouds_or_refresh', lambda *args, **kwargs: [FakeCloud()])

        monkeypatch.setattr(sky.optimizer.Optimizer, '_optimize_dag', lambda *args, **kwargs: {task: task.resources})

        backend = FakeBackend(cluster_name, provision_latency_s=1, setup_latency_s=1, sync_workdir_latency_s=0, add_storage_objects_latency_s=0, sync_file_mounts_latency_s=0, exec_code_on_head_latency_s=0, execute_latency_s=0, teardown_latency_s=0)
        benchmark.pedantic(
            execution.launch,
            kwargs={
                "task": SIMPLE_TASK,
                "cluster_name": cluster_name,
                "stream_logs": False,
                "backend": backend,
            },
            iterations=1,
            rounds=ROUNDS_PER_TEST)