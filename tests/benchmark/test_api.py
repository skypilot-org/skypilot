import pytest
from unittest import mock
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
from sky import clouds
from sky.backends import cloud_vm_ray_backend
from sky.backends.cloud_vm_ray_backend import RetryingVmProvisioner as RealRetryingVmProvisioner
from sky import execution
from sky import dag
from sky.client import sdk
import time
from itertools import chain
from dataclasses import dataclass
from sky.utils import status_lib
import copy
import dataclasses
import enum
import inspect
import json
import math
import os
import pathlib
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import typing
from typing import (Any, Callable, Dict, Iterable, List, Optional, Set, Tuple,
                    Union)

import colorama
import psutil

from sky import backends
from sky import catalog
from sky import check as sky_check
from sky import cloud_stores
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import jobs as managed_jobs
from sky import optimizer
from sky import provision as provision_lib
from sky import resources as resources_lib
from sky import sky_logging
from sky import skypilot_config
from sky import task as task_lib
from sky.adaptors import common as adaptors_common
from sky.backends import backend_utils
from sky.backends import wheel_utils
from sky.clouds import cloud as sky_cloud
from sky.clouds.utils import gcp_utils
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.provision import common as provision_common
from sky.provision import instance_setup
from sky.provision import metadata_utils
from sky.provision import provisioner
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.server.requests import requests as requests_lib
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.usage import usage_lib
from sky.utils import accelerator_registry
from sky.utils import annotations
from sky.utils import cluster_utils
from sky.utils import command_runner
from sky.utils import common
from sky.utils import common_utils
from sky.utils import context_utils
from sky.utils import controller_utils
from sky.utils import directory_utils
from sky.utils import env_options
from sky.utils import lock_events
from sky.utils import locks
from sky.utils import log_utils
from sky.utils import message_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils
from sky.utils import volume as volume_lib
from sky.utils import yaml_utils

import functools

if typing.TYPE_CHECKING:
    import grpc

    from sky import dag
    from sky.schemas.generated import autostopv1_pb2
    from sky.schemas.generated import autostopv1_pb2_grpc
else:
    # To avoid requiring grpcio to be installed on the client side.
    grpc = adaptors_common.LazyImport('grpc')
    autostopv1_pb2 = adaptors_common.LazyImport(
        'sky.schemas.generated.autostopv1_pb2')
    autostopv1_pb2_grpc = adaptors_common.LazyImport(
        'sky.schemas.generated.autostopv1_pb2_grpc')

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
    def test_sdk_api_launch(self,
        benchmark: BenchmarkFixture,
        generic_cloud: str,
        num_clusters: int,
        rounds: int):
        names = []
        for i in range(num_clusters):
            names.append(get_cluster_name() + "-lloyd-" + str(i))

        def down_clusters():
            request_ids = [sdk.down(name) for name in names]
            for req_id in request_ids:
                try:
                    sdk.get(req_id)
                except Exception as e:
                    pass

        def submit_and_get_request_id():
            ids = [sdk.launch(task=SIMPLE_TASK, cluster_name=name) for name in names]
            rets = [
                sdk.get(id) for id in ids]
            return rets

        benchmark.pedantic(
            submit_and_get_request_id,
            setup=down_clusters,
            iterations=1,
            rounds=rounds)
        down_clusters()

    # def test_sdk_api_down(self, benchmark: BenchmarkFixture, generic_cloud: str):
    #     name = get_cluster_name() + "-lloyd"
    #     simple_task = sky.Task(
    #         run='echo hello SkyPilot',
    #         resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
    #     )

    #     def down_cluster_then_launch():
    #         req_id = sdk.down(name)
    #         try:
    #             sdk.get(req_id)
    #         except sky.exceptions.ClusterDoesNotExist as e:
    #             pass

    #         req_id = sdk.launch(task=simple_task, cluster_name=name)
    #         sdk.get(req_id)


    #     def submit_and_get_request_id():
    #         req_id = sdk.down(cluster_name=name)
    #         return sdk.get(req_id)

    #     benchmark.pedantic(
    #         submit_and_get_request_id,
    #         kwargs={},
    #         setup=down_cluster_then_launch,
    #         iterations=1,
    #         rounds=ROUNDS_PER_TEST)

    # def test_sdk_api_tail_logs(self, benchmark: BenchmarkFixture, generic_cloud: str):
    #     name = get_cluster_name() + "-lloyd"
    #     simple_task = sky.Task(
    #         run='echo hello SkyPilot',
    #         resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
    #     )

    #     job_id = None

    #     def teardown():
    #         nonlocal job_id
    #         if job_id is not None:
    #             req_id = sdk.down(name)
    #             sdk.get(req_id)
    #             job_id = None

    #     def launch_cluster():
    #         nonlocal job_id
    #         teardown()
    #         req_id = sdk.launch(task=simple_task, cluster_name=name)
    #         job_id = sdk.get(req_id)[0]
    #         _wait_for_cluster_to_be_up(name)
        
    #     def tail_logs():
    #         nonlocal job_id
    #         try:
    #             req_id = sdk.tail_logs(cluster_name=name, job_id=job_id, follow=False)
    #             return sdk.get(req_id)
    #         except Exception as e:
    #             return str(e)

    #     res = benchmark.pedantic(
    #         tail_logs,
    #         kwargs={},
    #         setup=launch_cluster,
    #         iterations=1,
    #         rounds=ROUNDS_PER_TEST)
    #     teardown()
    #     assert "Job finished (status: SUCCEEDED)." in res

    def test_sdk_status(self,
        benchmark: BenchmarkFixture,
        generic_cloud: str,
        num_clusters: int,
        rounds: int):
        names = []
        for i in range(num_clusters):
            names.append(get_cluster_name() + "-lloyd-" + str(i))
        
        def down_clusters():
            request_ids = [sdk.down(name) for name in names]
            for req_id in request_ids:
                try:
                    sdk.get(req_id)
                except Exception as e:
                    pass

        def launch_clusters():
            ids = [sdk.launch(task=SIMPLE_TASK, cluster_name=name) for name in names]
            rets = [
                sdk.get(id) for id in ids]
            return rets

        def status():
            req_id = sdk.status(cluster_names=names)
            res = sdk.get(req_id)
            assert len(res) == len(names)
            for r in res:
                assert r['status'] == sky.ClusterStatus.UP
                assert r['name'] in names

        launch_clusters()
        benchmark.pedantic(
            status,
            iterations=1,
            rounds=rounds)
        down_clusters()

    # def test_sdk_status_refresh(self, benchmark: BenchmarkFixture, generic_cloud: str):
    #     name = get_cluster_name() + "-lloyd"
    #     simple_task = sky.Task(
    #         run='echo hello SkyPilot',
    #         resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
    #     )

    #     def teardown():
    #         req_id = sdk.down(name)
    #         sdk.get(req_id)

    #     def launch_cluster():
    #         req_id = sdk.launch(task=simple_task, cluster_name=name)
    #         sdk.get(req_id)[0]
    #         _wait_for_cluster_to_be_up(name)

    #     def status():
    #         req_id = sdk.status(cluster_names=[name], refresh=sky.StatusRefreshMode.FORCE)
    #         return sdk.get(req_id)

    #     launch_cluster()
    #     res = benchmark.pedantic(
    #         status,
    #         iterations=1,
    #         rounds=ROUNDS_PER_TEST)
    #     assert res[0]['status'] == sky.ClusterStatus.UP
    #     assert res[0]['name'] == name
    #     teardown()

    # def test_sdk_check(self, benchmark: BenchmarkFixture, generic_cloud: str):
    #     def check():
    #         req_id = sdk.check(infra_list=[generic_cloud], verbose=False)
    #         return sdk.get(req_id)

    #     res = benchmark.pedantic(
    #         check,
    #         iterations=1,
    #         rounds=ROUNDS_PER_TEST)
    #     enabled_clouds = list(chain.from_iterable(res.values()))
    #     enabled_clouds = [cloud.lower() for cloud in enabled_clouds]
    #     assert generic_cloud in enabled_clouds, f"{generic_cloud} not in enabled."

# class FakeResourceHandle(sky.backends.ResourceHandle):
#     def __init__(self, cluster_name: str):
#         self.cluster_name = cluster_name

#     def get_cluster_name(self) -> str:
#         return self.cluster_name

# LLOYD: _check_existing_cluster latency: 0.0007009506225585938 seconds
# LLOYD: usage_lib.messages.usage.update_cluster_resources latency: 5.793571472167969e-05 seconds
# LLOYD: cloud.zones_provision_loop latency: 2.6226043701171875e-06 seconds
# LLOYD: _get_cluster_config_template latency: 1.5735626220703125e-05 seconds
# LLOYD: _add_auth_to_cluster_config latency: 0.31740617752075195 seconds
# ⚙︎ Launching on Kubernetes.
# LLOYD: bootstrap_instances latency: 1.6307051181793213 seconds
# LLOYD: run_instances latency: 3.1041672229766846 seconds
# LLOYD: wait_instances latency: 0.001013040542602539 seconds
# LLOYD: make_deploy_resources_variables latency: 0.0015938282012939453 seconds
# LLOYD: _retry_zones latency: 6.947166204452515 seconds
# └── Pod is up.
# LLOYD: setup_runtime_on_cluster latency: 2.1137232780456543 seconds
# LLOYD: start_skylet_on_head_node latency: 3.3228938579559326 seconds
# ✓ Cluster launched: sky-ef3c-lloyd.  View logs: sky logs --provision sky-ef3c-lloyd
# Provision latency: 30.551822900772095 seconds
# ⚙︎ Syncing files.
# Setup latency: 2.1457672119140625e-06 seconds
# ⚙︎ Job submitted, ID: 1
# Execute latency: 4.693678855895996 seconds

# @dataclass
# class BackendLatency:    
#     provision_latency_s: float
#     setup_latency_s: float
#     sync_workdir_latency_s: float
#     add_storage_objects_latency_s: float
#     sync_file_mounts_latency_s: float
#     exec_code_on_head_latency_s: float
#     execute_latency_s: float
#     teardown_latency_s: float
#     check_existing_cluster_latency_s: float
#     update_cluster_resources_latency_s: float
#     retry_zones_latency_s: float
#     setup_runtime_on_cluster_latency_s: float
#     start_skylet_on_head_node_latency_s: float
#     _query_head_ip_with_retries_latency_s: float
#     _add_auth_to_cluster_config_latency_s: float
#     wait_until_ray_cluster_ready_latency_s: float
#     setup_logging_on_cluster_latency_s: float
#     bootstrap_instances_latency_s: float
#     wait_instances_latency_s: float


# KUBERNETES_BACKEND_LATENCY = BackendLatency(
#     provision_latency_s=30,
#     setup_latency_s=1, 
#     sync_workdir_latency_s=1, #TODO: Check if this is correct
#     add_storage_objects_latency_s=1, #TODO: Check if this is correct
#     sync_file_mounts_latency_s=1, 
#     exec_code_on_head_latency_s=1,
#     execute_latency_s=1,
#     teardown_latency_s=6.5)

# FAST_BACKEND_LATENCY = BackendLatency(
#     provision_latency_s=1,
#     setup_latency_s=0.1,
#     sync_workdir_latency_s=0.1,
#     add_storage_objects_latency_s=0.1,
#     sync_file_mounts_latency_s=0.1,
#     exec_code_on_head_latency_s=0.1,
#     execute_latency_s=0.1,
#     teardown_latency_s=0.1,
#     bootstrap_instances_latency_s=0.1,
#     wait_instances_latency_s=0.1,
#     check_existing_cluster_latency_s=0.1,
#     update_cluster_resources_latency_s=0.1,
#     retry_zones_latency_s=0.1,
#     setup_runtime_on_cluster_latency_s=0.1,
#     start_skylet_on_head_node_latency_s=0.1,
#     _query_head_ip_with_retries_latency_s=0.1,
#     _add_auth_to_cluster_config_latency_s=0.1,
#     wait_until_ray_cluster_ready_latency_s=0.1,
#     setup_logging_on_cluster_latency_s=0.1,
#     start_ray_on_head_node_latency_s=0.1,
#     start_ray_on_worker_nodes_latency_s=0.1)

# class FakeRetryingVmProvisioner(RealRetryingVmProvisioner):
#     def __init__(self,
#                 log_dir: str,
#                  dag: dag.Dag,
#                  optimize_target: common.OptimizeTarget,
#                  requested_features: Set[clouds.CloudImplementationFeatures],
#                  local_wheel_path: Path,
#                  wheel_hash: str,
#                  blocked_resources: Optional[Iterable[
#                  resources_lib.Resources]] = None,
#                  is_managed: Optional[bool] = None,
#                  backend_latency: BackendLatency = KUBERNETES_BACKEND_LATENCY):
#         self.backend_latency = backend_latency
#         super().__init__(log_dir,
#                          dag,
#                          optimize_target,
#                          requested_features,
#                          local_wheel_path,
#                          wheel_hash,
#                          blocked_resources,
#                          is_managed)

#     def _gang_schedule_ray_up(
#         self, to_provision_cloud: clouds.Cloud, cluster_config_file: str,
#         cluster_handle: 'backends.CloudVmRayResourceHandle', log_abs_path: str,
#         stream_logs: bool, logging_info: dict, use_spot: bool
#     ) -> Tuple[cloud_vm_ray_backend.GangSchedulingStatus, str, str, Optional[str], Optional[str]]:
#         time.sleep(self.backend_latency.provision_latency_s)
#         return cloud_vm_ray_backend.GangSchedulingStatus.CLUSTER_READY, "", "", None, None

#     def _ensure_cluster_ray_started(self, handle: 'backends.CloudVmRayResourceHandle',
#                                     log_abs_path) -> None:
#         return

# class FakeBackend(cloud_vm_ray_backend.CloudVmRayBackend):
#     def __init__(self, cluster_name: str,
#                  backend_latency: BackendLatency):
#         self.cluster_name = "FAKE-" + cluster_name
#         self.backend_latency = backend_latency
#         self.job_id = 0
#         self.resource_handle = FakeResourceHandle(cluster_name)
#         super().__init__()

#     def check_resource_fit_cluster(self, handle: sky.backends.ResourceHandle, task: sky.Task):
#         return task.resources
    
#     def _sync_workdir(self, handle: FakeResourceHandle,
#                       workdir: Union[Path, Dict[str, Any]],
#                       envs_and_secrets: Dict[str, str]) -> None:
#         time.sleep(self.backend_latency.sync_workdir_latency_s)
    
#     def _sync_file_mounts(
#         self,
#         handle: FakeResourceHandle,
#         all_file_mounts: Optional[Dict[Path, Path]],
#         storage_mounts: Optional[Dict[Path, 'storage_lib.Storage']],
#     ) -> None:
#         time.sleep(self.backend_latency.sync_file_mounts_latency_s)

#     def add_storage_objects(self, task: task_lib.Task) -> None:
#         time.sleep(self.backend_latency.add_storage_objects_latency_s)

#     def _setup(self, handle: FakeResourceHandle, task: task_lib.Task,
#                detach_setup: bool) -> None:
#         time.sleep(self.backend_latency.setup_latency_s)

#     def _exec_code_on_head(
#         self,
#         handle: FakeResourceHandle,
#         codegen: str,
#         job_id: int,
#         detach_run: bool = False,
#         managed_job_dag: Optional[dag.Dag] = None,
#         remote_log_dir: Optional[str] = None,
#     ) -> None:
#         time.sleep(self.backend_latency.exec_code_on_head_latency_s)

#     def _execute(self,
#                 handle: FakeResourceHandle,
#                 task: task_lib.Task,
#                 detach_run: bool,
#                 dryrun: bool = False) -> Optional[int]:
#         job_id = self.job_id
#         self.job_id += 1
#         print(f"Submitting job {job_id}")
#         time.sleep(self.backend_latency.execute_latency_s)
#         print(f"Job {job_id} completed")
#         return job_id
    
#     def _post_execute(self, handle: FakeResourceHandle, down: bool) -> None:
#         pass

#     def _teardown_ephemeral_storage(self, task: task_lib.Task) -> None:
#         pass
    
#     def _teardown(self, handle: FakeResourceHandle, terminate: bool, purge: bool = False) -> None:
#         time.sleep(self.backend_latency.teardown_latency_s)

# class FakeCloud(sky.clouds.Cloud):
#     _REPR = 'FakeCloud'
#     STATUS_VERSION = clouds.StatusVersion.SKYPILOT
#     PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
#     _INDENT_PREFIX = '    '
#     _STATIC_CREDENTIAL_HELP_STR = (
#         'Run the following commands:'
#         f'\n{_INDENT_PREFIX}  $ aws configure'
#         f'\n{_INDENT_PREFIX}  $ aws configure list  # Ensure that this shows identity is set.'
#         f'\n{_INDENT_PREFIX}For more info: '
#         'https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-quickstart.html'  # pylint: disable=line-too-long
#     )
#     _SUPPORTED_DISK_TIERS = set(resources_utils.DiskTier)
#     _REGION = sky.clouds.Region(name="FakeRegion")
#     _ZONE = sky.clouds.Zone(name="FakeZone")
    
#     def __init__(self, backend_latency: BackendLatency):
#         self.name = "FakeCloud"
#         self.cluster_to_num_nodes = {}
#         self.backend_latency = backend_latency

#     def regions_with_offering(self, instance_type: str, accelerators: Optional[Dict[str, int]], use_spot: bool, region: Optional[str], zone: Optional[str]) -> List[sky.clouds.Region]:
#         return [self._REGION]

#     def zones_provision_loop(
#         cls,
#         *,
#         region: str,
#         num_nodes: int,
#         instance_type: str,
#         accelerators: Optional[Dict[str, int]] = None,
#         use_spot: bool = False,
#     ) -> Iterator[Optional[List[sky.clouds.Zone]]]:
#         time.sleep(cls.backend_latency.provision_latency_s)
#         yield [cls._ZONE]
    
#     def check_features_are_supported(
#         cls, resources: resources_lib.Resources,
#         requested_features: Set[CloudImplementationFeatures]) -> None:
#         pass

#     def get_feasible_launchable_resources(
#             self,
#             resources: 'resources_lib.Resources',
#             num_nodes: int = 1) -> 'resources_utils.FeasibleResources':
#         return resources_utils.FeasibleResources([resources], [], None)

#     def check_quota_available(self, resources: resources_lib.Resources) -> bool:
#         return True

#     @classmethod
#     def get_accelerators_from_instance_type(
#         cls,
#         instance_type: str,
#     ) -> Optional[Dict[str, Union[int, float]]]:
#         return None

#     def instance_type_exists(self, instance_type: str) -> bool:
#         return True

#     def get_vcpus_mem_from_instance_type(
#             cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
#         """Returns the #vCPUs and memory that the instance type offers."""
#         return (2.0, 4.0)
    
#     def make_deploy_resources_variables(
#         self,
#         resources: 'resources_lib.Resources',
#         cluster_name: resources_utils.ClusterName,
#         region: 'clouds.Region',
#         zones: Optional[List['clouds.Zone']],
#         num_nodes: int,
#         dryrun: bool = False,
#         volume_mounts: Optional[List['sky.volume_lib.VolumeMount']] = None,
#     ) -> Dict[str, Any]:
#         # return {}
#         # print(f"LLOYD: cluster_to_num_nodes in make_deploy_resources_variables: {self.cluster_to_num_nodes}")
#         # print(f"LLOYD: cluster_name in make_deploy_resources_variables: {cluster_name}")
#         # print(f"LLOYD: cluster_name type in make_deploy_resources_variables: {type(cluster_name)}")
#         self.cluster_to_num_nodes[str(cluster_name)] = num_nodes
#         # print(f"LLOYD: cluster_to_num_nodes in make_deploy_resources_variables: {self.cluster_to_num_nodes}")
#         return { 
#             'instance_type': "FakeInstance",
#             'custom_resources': None,
#             'cpus': str(2.0),
#             'memory': str(4.0),
#             'accelerator_count': str(0),
#             'timeout': str(100),
#             'k8s_port_mode': 0,
#             'k8s_networking_mode': 0,
#             'k8s_ssh_key_secret_name': "FakeSSHKeySecretName",
#             'k8s_acc_label_key': "FakeAccLabelKey",
#             'k8s_acc_label_values': "FakeAccLabelValues",
#             'k8s_ssh_jump_name': "FakeSSHJumpName",
#             'k8s_ssh_jump_image': "FakeSSHJumpImage",
#             'k8s_service_account_name': "FakeServiceAccountName",
#             'k8s_automount_sa_token': 'true',
#             'k8s_fuse_device_required': False,
#             'k8s_kueue_local_queue_name': "FakeKueueLocalQueueName",
#             # Namespace to run the fusermount-server daemonset in
#             'k8s_skypilot_system_namespace': "FakeSkypilotSystemNamespace",
#             'k8s_fusermount_shared_dir': "FakeFusermountSharedDir",
#             'k8s_spot_label_key': "FakeSpotLabelKey",
#             'k8s_spot_label_value': "FakeSpotLabelValue",
#             'tpu_requested': False,
#             'k8s_topology_label_key': "FakeTopologyLabelKey",
#             'k8s_topology_label_value': "FakeTopologyLabelValue",
#             'k8s_resource_key': "FakeResourceKey",
#             'k8s_env_vars': {},
#             'image_id': "FakeImageId",
#             'ray_installation_commands': constants.RAY_INSTALLATION_COMMANDS,
#             'ray_head_start_command': "FakeRayHeadStartCommand",
#             'skypilot_ray_port': constants.SKY_REMOTE_RAY_PORT,
#             'ray_worker_start_command': "FakeRayWorkerStartCommand",
#             'k8s_high_availability_deployment_volume_mount_name':
#                 (kubernetes_utils.HIGH_AVAILABILITY_DEPLOYMENT_VOLUME_MOUNT_NAME
#                 ),
#             'k8s_high_availability_deployment_volume_mount_path':
#                 (kubernetes_utils.HIGH_AVAILABILITY_DEPLOYMENT_VOLUME_MOUNT_PATH
#                 ),
#             'k8s_high_availability_deployment_setup_script_path':
#                 (constants.PERSISTENT_SETUP_SCRIPT_PATH),
#             'k8s_high_availability_deployment_run_script_dir':
#                 (constants.PERSISTENT_RUN_SCRIPT_DIR),
#             'k8s_high_availability_restarting_signal_file':
#                 (constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE),
#             'ha_recovery_log_path':
#                 constants.HA_PERSISTENT_RECOVERY_LOG_PATH.format(''),
#             'sky_python_cmd': constants.SKY_PYTHON_CMD,
#             'k8s_high_availability_storage_class_name':
#                 ("FakeK8sHaStorageClassName"),
#             'avoid_label_keys': None,
#             'k8s_enable_flex_start': False,
#             'k8s_max_run_duration_seconds': 100,
#             'k8s_network_type': 0,
#         }

#     def query_instances(
#         self,
#         cluster_name: str,
#     ) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:

#         if cluster_name in self.cluster_to_num_nodes:
#             return {
#                 f"FakeInstance-{i}": (status_lib.ClusterStatus.UP, None)
#                 for i in range(self.cluster_to_num_nodes[cluster_name])
#             }
#         else:
#             return {}       

#     def bootstrap_instances(
#         self,
#         region: str, cluster_name: str,
#         config: sky.provision.common.ProvisionConfig) -> sky.provision.common.ProvisionConfig:
#         return config
    
#     def run_instances(
#         self,
#         region: str, cluster_name: str,
#         config: sky.provision.common.ProvisionConfig) -> sky.provision.common.ProvisionRecord:
#         self.cluster_to_num_nodes[cluster_name] = config.count
#             # simplified_cluster_name = cluster_name[:cluster_name.rfind('-')]
#             # print(f"LLOYD: simplified_cluster_name: {simplified_cluster_name}")
#         num_nodes = self.cluster_to_num_nodes.get(cluster_name)
#         return sky.provision.common.ProvisionRecord(
#             provider_name='FakeCloud', 
#             region=region, 
#             zone=self._ZONE.name,
#             cluster_name=cluster_name,
#             head_instance_id=0,
#             resumed_instance_ids=[],
#             created_instance_ids=[i for i in range(1, num_nodes + 1)])
    
#     def wait_instances(
#         self,
#         provider_name: str, region: str, cluster_name_on_cloud: str,
#         state: Optional['status_lib.ClusterStatus']) -> None:
#         return None

#     def get_cluster_info(
#         self,
#         region: str,
#         cluster_name_on_cloud: str,
#         provider_config: Optional[Dict[str, Any]] = None) -> sky.provision.common.ClusterInfo:
#         num_nodes = self.cluster_to_num_nodes.get(cluster_name_on_cloud)
#         head_instance_id = 0 if num_nodes > 0 else None

#         instances = {}
#         for inst in range(num_nodes):
#             instances[inst] = [
#                 sky.provision.common.InstanceInfo(
#                     instance_id=inst,
#                     internal_ip=f"127.0.0.{inst}",
#                     external_ip=f"127.0.0.{inst}",
#                     tags={},
#                 )
#             ]
#         return sky.provision.common.ClusterInfo(
#             instances=instances,
#             head_instance_id=head_instance_id,
#             provider_name='FakeCloud',
#             provider_config=provider_config,
#             docker_user='ubuntu',
#             ssh_user='ubuntu',
#             custom_ray_options={},
#         )

#     def wait_for_ssh(self, cluster_info: sky.provision.common.ClusterInfo, ssh_credentials: Dict[str, str]) -> None:
#         return

# class TestScaleSDKAPI:
#     def test_sdk_scale_launch(self,
#                                 monkeypatch: pytest.MonkeyPatch,
#                                 benchmark: BenchmarkFixture,
#                                 generic_cloud: str,
#                                 num_clusters: int = 1,
#                                 check_capabilities_latency_s: float = 0):
#         backend_latency = FAST_BACKEND_LATENCY
#         fake_cloud = FakeCloud(backend_latency=backend_latency)

#         monkeypatch.setattr(sky.utils.registry.CLOUD_REGISTRY, 'from_str', lambda *args, **kwargs: fake_cloud)
#         task = sky.Task(
#             run='echo hello SkyPilot',
#             resources=sky.Resources(cpus='2', memory='4GB', infra='FakeCloud/FakeRegion/FakeZone', instance_type="FakeInstance")
#         )
#         print(f"task before dag insertion: {task}")
#         cluster_name = f"cluster-1"

#         # Monkeypatch this so that isinstance(backend, backends.CloudVmRayBackend) is True
#         # monkeypatch.setattr(sky.backends, 'CloudVmRayBackend', FakeBackend)

#         def check_capabilities(*args, **kwargs):
#             time.sleep(check_capabilities_latency_s)
#             return {"default": {"FakeCloud": ["compute"]}}
#         monkeypatch.setattr(sky.check, 'check_capabilities', check_capabilities)

#         # print(f"LLOYD: fake_cloud cluster_to_num_nodes: {fake_cloud.cluster_to_num_nodes}")

#         monkeypatch.setattr(sky.check, 'get_cached_enabled_clouds_or_refresh', lambda *args, **kwargs: [fake_cloud])

#         # task_dag = dag.Dag()
#         # task_dag.add(task)
#         # print(f"task_dag at test begin: {task_dag}")

#         def optimize_dag(*args, **kwargs):
#             task_dag = kwargs['dag']
#             best_plan = {}
#             for idx, task in enumerate(task_dag.tasks):
#                 task_dag.tasks[idx].best_resources = list(task_dag.tasks[idx].resources)[0]
#                 best_plan[task_dag.tasks[idx]] = task_dag.tasks[idx].best_resources
#             #     print(f"LLOYD: task_dag.tasks[0].best_resources at optimize_dag: {task_dag.tasks[0].best_resources}")
#             # print(f"LLOYD: best_plan at optimize_dag: {best_plan}")
#             return best_plan
#         monkeypatch.setattr(sky.optimizer.Optimizer, '_optimize_dag', lambda *args, **kwargs: optimize_dag(*args, **kwargs))
#         # monkeypatch.setattr(sky.optimizer.Optimizer, '_optimize_dag', lambda *args, **kwargs: task_dag)

#         monkeypatch.setattr('sky.backends.backend_utils.wait_until_ray_cluster_ready', lambda *args, **kwargs: (True, None))

#         monkeypatch.setattr('sky.resources.Resources.is_launchable', lambda self: True)

#         extra = {"backend_latency": backend_latency}
#         fake_provisioner_factory = functools.partial(FakeRetryingVmProvisioner, **extra)
#         # Add the ToProvisionConfig as a class attribute to the partial
#         fake_provisioner_factory.ToProvisionConfig = RealRetryingVmProvisioner.ToProvisionConfig

#         monkeypatch.setattr(cloud_vm_ray_backend, 'RetryingVmProvisioner', fake_provisioner_factory)
        
#         monkeypatch.setattr('sky.resources.Resources._try_validate_and_set_region_zone', lambda self: None)

#         monkeypatch.setattr('sky.backends.cloud_vm_ray_backend._get_cluster_config_template', lambda *args: 'kubernetes-ray.yml.j2')

#         monkeypatch.setattr('sky.backends.backend_utils._add_auth_to_cluster_config', lambda *args: {})
#         monkeypatch.setattr('sky.backends.backend_utils._query_head_ip_with_retries', lambda *args, **kwargs: '127.0.0.1')
#         # _query_head_ip_with_retries

#         mock_command_runner = mock.Mock()
#         mock_command_runner.run = lambda *args, **kwargs: 0
#         monkeypatch.setattr('sky.provision.get_command_runners', lambda *args, **kwargs: [mock_command_runner])

#         monkeypatch.setattr('sky.provision.query_instances', lambda *args, **kwargs: fake_cloud.query_instances(args[1]))
#         monkeypatch.setattr('sky.provision.bootstrap_instances', lambda *args, **kwargs: fake_cloud.bootstrap_instances(args[1], args[2], args[3]))
#         monkeypatch.setattr('sky.provision.run_instances', lambda *args, **kwargs: fake_cloud.run_instances(args[1], args[2], config=kwargs['config']))
#         monkeypatch.setattr('sky.provision.wait_instances', lambda *args, **kwargs: fake_cloud.wait_instances(args[1], args[2], cluster_name_on_cloud=kwargs.get('cluster_name_on_cloud'), state=kwargs.get('state')))
#         monkeypatch.setattr('sky.provision.get_cluster_info', lambda *args, **kwargs: fake_cloud.get_cluster_info(args[1], args[2], provider_config=kwargs.get('provider_config')))
#         monkeypatch.setattr('sky.provision.provisioner.wait_for_ssh', lambda *args, **kwargs: fake_cloud.wait_for_ssh(args[0], args[1]))
#         monkeypatch.setattr('sky.provision.instance_setup.internal_file_mounts', lambda *args, **kwargs: None)
#         monkeypatch.setattr('sky.provision.instance_setup.setup_runtime_on_cluster', lambda *args, **kwargs: None)
#         monkeypatch.setattr('sky.provision.instance_setup.setup_logging_on_cluster', lambda *args, **kwargs: None)
#         monkeypatch.setattr('sky.provision.instance_setup.start_ray_on_head_node', lambda *args, **kwargs: None)
#         monkeypatch.setattr('sky.provision.instance_setup.start_ray_on_worker_nodes', lambda *args, **kwargs: None)
#         monkeypatch.setattr('sky.provision.instance_setup.start_skylet_on_head_node', lambda *args, **kwargs: None)
#         monkeypatch.setattr('sky.provision.instance_setup.setup_runtime_on_cluster', lambda *args, **kwargs: None)
#         monkeypatch.setattr('sky.provision.instance_setup.setup_logging_on_cluster', lambda *args, **kwargs: None)

#         monkeypatch.setattr('sky.provision.common.ProvisionRecord.is_instance_just_booted', lambda self, instance_id: True)
#         extra = {"backend_latency": backend_latency, "cluster_name": cluster_name}
#         fake_backend_factory = functools.partial(FakeBackend, **extra)
#         # Patch the constructor for CloudVmRayBackend.
#         monkeypatch.setattr(cloud_vm_ray_backend, 'CloudVmRayBackend', fake_backend_factory)

#         monkeypatch.setattr('sky.provision.cleanup_ports', lambda *args, **kwargs: None)
#         monkeypatch.setattr('sky.provision.cleanup_custom_multi_network', lambda *args, **kwargs: None)


#         backend = FakeBackend(cluster_name, backend_latency)
#         benchmark.pedantic(
#             execution.launch,
#             kwargs={
#                 "task": task,
#                 "cluster_name": cluster_name,
#                 "stream_logs": False,
#                 "backend": backend,
#             },
#             iterations=1,
#             rounds=ROUNDS_PER_TEST)