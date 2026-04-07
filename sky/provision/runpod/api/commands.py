"""This module provides functions for deploying Pods on RunPod, including
on-demand, spot, and CPU instances.

Reference:
    https://github.com/runpod/runpod-python/blob/main/runpod/api/ctl_commands.py
"""
from typing import List, Optional

from sky.adaptors import runpod
from sky.provision.runpod.api import pods as pod_mutations

_INTERRUPTABLE_POD_FIELD: str = 'podRentInterruptable'
_ON_DEMAND_POD_FIELD: str = 'podFindAndDeployOnDemand'
_CPU_POD_FIELD: str = 'deployCpuPod'
_RESPONSE_DATA_FIELD: str = 'data'


def create_spot_pod(
    name: str,
    image_name: str,
    gpu_type_id: str,
    bid_per_gpu: float,
    cloud_type: str = 'ALL',
    volume_mount_path: str = '/runpod-volume',
    gpu_count: Optional[int] = 1,
    min_memory_in_gb: Optional[int] = 1,
    min_vcpu_count: Optional[int] = 1,
    container_disk_in_gb: Optional[int] = None,
    volume_in_gb: Optional[int] = 0,
    ports: Optional[str] = None,
    start_ssh: Optional[bool] = True,
    start_jupyter: Optional[bool] = False,
    env: Optional[dict] = None,
    docker_args: Optional[str] = '',
    support_public_ip: Optional[bool] = True,
    terminate_after: Optional[str] = None,
    stop_after: Optional[str] = None,
    data_center_id: Optional[str] = None,
    country_code: Optional[str] = None,
    network_volume_id: Optional[str] = None,
    allowed_cuda_versions: Optional[List[str]] = None,
    min_download: Optional[int] = None,
    min_upload: Optional[int] = None,
    cuda_version: Optional[str] = None,
    template_id: Optional[str] = None,
    volume_key: Optional[str] = None,
    container_registry_auth_id: Optional[str] = None,
) -> dict:
    """Creates a spot (interruptable) GPU pod on RunPod."""
    runpod.runpod.get_gpu(gpu_type_id)
    # refer to https://graphql-spec.runpod.io/#definition-CloudTypeEnum
    if cloud_type not in ['ALL', 'COMMUNITY', 'SECURE']:
        raise ValueError('cloud_type must be one of ALL, COMMUNITY or SECURE')

    if network_volume_id and data_center_id is None:
        user_info = runpod.runpod.get_user()
        for network_volume in user_info['networkVolumes']:
            if network_volume['id'] == network_volume_id:
                data_center_id = network_volume['dataCenterId']
                break

    if container_disk_in_gb is None and template_id is None:
        container_disk_in_gb = 10

    mutation = pod_mutations.generate_spot_pod_deployment_mutation(
        name=name,
        image_name=image_name,
        gpu_type_id=gpu_type_id,
        bid_per_gpu=bid_per_gpu,
        cloud_type=cloud_type,
        gpu_count=gpu_count,
        min_memory_in_gb=min_memory_in_gb,
        min_vcpu_count=min_vcpu_count,
        container_disk_in_gb=container_disk_in_gb,
        volume_in_gb=volume_in_gb,
        volume_mount_path=volume_mount_path,
        ports=ports,
        start_ssh=start_ssh,
        start_jupyter=start_jupyter,
        env=env,
        docker_args=docker_args,
        support_public_ip=support_public_ip,
        terminate_after=terminate_after,
        stop_after=stop_after,
        data_center_id=data_center_id,
        country_code=country_code,
        network_volume_id=network_volume_id,
        allowed_cuda_versions=allowed_cuda_versions,
        min_download=min_download,
        min_upload=min_upload,
        cuda_version=cuda_version,
        template_id=template_id,
        volume_key=volume_key,
        container_registry_auth_id=container_registry_auth_id,
    )
    response = runpod.runpod.api.graphql.run_graphql_query(mutation)
    return response[_RESPONSE_DATA_FIELD][_INTERRUPTABLE_POD_FIELD]


def create_on_demand_pod(
    name: str,
    image_name: str,
    gpu_type_id: Optional[str] = None,
    instance_id: Optional[str] = None,
    cloud_type: str = 'ALL',
    volume_mount_path: str = '/runpod-volume',
    gpu_count: Optional[int] = 1,
    min_memory_in_gb: Optional[int] = 1,
    min_vcpu_count: Optional[int] = 1,
    container_disk_in_gb: Optional[int] = None,
    volume_in_gb: Optional[int] = 0,
    ports: Optional[str] = None,
    start_ssh: Optional[bool] = True,
    start_jupyter: Optional[bool] = False,
    env: Optional[dict] = None,
    docker_args: Optional[str] = '',
    support_public_ip: Optional[bool] = True,
    terminate_after: Optional[str] = None,
    stop_after: Optional[str] = None,
    data_center_id: Optional[str] = None,
    country_code: Optional[str] = None,
    network_volume_id: Optional[str] = None,
    allowed_cuda_versions: Optional[List[str]] = None,
    min_download: Optional[int] = None,
    min_upload: Optional[int] = None,
    cuda_version: Optional[str] = None,
    template_id: Optional[str] = None,
    volume_key: Optional[str] = None,
    container_registry_auth_id: Optional[str] = None,
) -> dict:
    """Creates an on-demand pod on RunPod (GPU or CPU).

    This custom function adds containerRegistryAuthId support for private
    Docker registry authentication, which the runpod SDK's create_pod()
    does not support.

    For GPU pods, gpu_type_id must be provided.
    For CPU pods, instance_id must be provided.
    """
    if network_volume_id and data_center_id is None:
        user_info = runpod.runpod.get_user()
        for network_volume in user_info['networkVolumes']:
            if network_volume['id'] == network_volume_id:
                data_center_id = network_volume['dataCenterId']
                break

    if container_disk_in_gb is None and template_id is None:
        container_disk_in_gb = 10

    if instance_id is not None:
        # CPU pod
        mutation = pod_mutations.generate_cpu_pod_deployment_mutation(
            name=name,
            image_name=image_name,
            instance_id=instance_id,
            container_disk_in_gb=container_disk_in_gb,
            volume_in_gb=volume_in_gb,
            volume_mount_path=volume_mount_path,
            ports=ports,
            start_ssh=start_ssh,
            start_jupyter=start_jupyter,
            env=env,
            docker_args=docker_args,
            support_public_ip=support_public_ip,
            terminate_after=terminate_after,
            stop_after=stop_after,
            data_center_id=data_center_id,
            country_code=country_code,
            network_volume_id=network_volume_id,
            template_id=template_id,
            volume_key=volume_key,
            container_registry_auth_id=container_registry_auth_id,
        )
        response = runpod.runpod.api.graphql.run_graphql_query(mutation)
        return response[_RESPONSE_DATA_FIELD][_CPU_POD_FIELD]
    else:
        # GPU pod
        assert gpu_type_id is not None, (
            'gpu_type_id is required for GPU pods')
        runpod.runpod.get_gpu(gpu_type_id)
        if cloud_type not in ['ALL', 'COMMUNITY', 'SECURE']:
            raise ValueError(
                'cloud_type must be one of ALL, COMMUNITY or SECURE')
        mutation = pod_mutations.generate_on_demand_gpu_pod_deployment_mutation(
            name=name,
            image_name=image_name,
            gpu_type_id=gpu_type_id,
            cloud_type=cloud_type,
            gpu_count=gpu_count,
            min_memory_in_gb=min_memory_in_gb,
            min_vcpu_count=min_vcpu_count,
            container_disk_in_gb=container_disk_in_gb,
            volume_in_gb=volume_in_gb,
            volume_mount_path=volume_mount_path,
            ports=ports,
            start_ssh=start_ssh,
            start_jupyter=start_jupyter,
            env=env,
            docker_args=docker_args,
            support_public_ip=support_public_ip,
            terminate_after=terminate_after,
            stop_after=stop_after,
            data_center_id=data_center_id,
            country_code=country_code,
            network_volume_id=network_volume_id,
            allowed_cuda_versions=allowed_cuda_versions,
            min_download=min_download,
            min_upload=min_upload,
            cuda_version=cuda_version,
            template_id=template_id,
            volume_key=volume_key,
            container_registry_auth_id=container_registry_auth_id,
        )
        response = runpod.runpod.api.graphql.run_graphql_query(mutation)
        return response[_RESPONSE_DATA_FIELD][_ON_DEMAND_POD_FIELD]
