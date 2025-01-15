"""This module provides functions to generate GraphQL mutations for deploying
spot instance Pods on RunPod.

Reference:
    https://github.com/runpod/runpod-python/blob/main/runpod/api/mutations/pods.py

Functions:
    generate_spot_pod_deployment_mutation: Generates a GraphQL mutation string
        for deploying a spot instance Pod on RunPod.
Example:
    >>> mutation = generate_spot_pod_deployment_mutation(
            name='test',
            image_name='runpod/stack',
            gpu_type_id='NVIDIA GeForce RTX 3070',
            bid_per_gpu=0.3
        )
"""

from typing import List, Optional


# refer to https://graphql-spec.runpod.io/#definition-PodRentInterruptableInput
def generate_spot_pod_deployment_mutation(
    name: str,
    image_name: str,
    gpu_type_id: str,
    bid_per_gpu: float,
    volume_mount_path: str,
    cloud_type: str = 'ALL',
    gpu_count: Optional[int] = None,
    min_memory_in_gb: Optional[int] = None,
    min_vcpu_count: Optional[int] = None,
    container_disk_in_gb: Optional[int] = None,
    volume_in_gb: Optional[int] = None,
    ports: Optional[str] = None,
    start_ssh: Optional[bool] = True,
    start_jupyter: Optional[bool] = False,
    env: Optional[dict] = None,
    docker_args: Optional[str] = None,
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
) -> str:
    input_fields = []

    # Required Fields
    input_fields.append(f'name: "{name}"')
    input_fields.append(f'imageName: "{image_name}"')
    input_fields.append(f'gpuTypeId: "{gpu_type_id}"')
    input_fields.append(f'bidPerGpu: {bid_per_gpu}')
    input_fields.append(f'volumeMountPath: "{volume_mount_path}"')

    # Default Fields
    input_fields.append(f'cloudType: {cloud_type}')

    if start_ssh:
        input_fields.append('startSsh: true')
    if start_jupyter:
        input_fields.append('startJupyter: true')
    if support_public_ip:
        input_fields.append('supportPublicIp: true')
    else:
        input_fields.append('supportPublicIp: false')

    # Optional Fields
    if gpu_count is not None:
        input_fields.append(f'gpuCount: {gpu_count}')
    if min_memory_in_gb is not None:
        input_fields.append(f'minMemoryInGb: {min_memory_in_gb}')
    if min_vcpu_count is not None:
        input_fields.append(f'minVcpuCount: {min_vcpu_count}')
    if container_disk_in_gb is not None:
        input_fields.append(f'containerDiskInGb: {container_disk_in_gb}')
    if volume_in_gb is not None:
        input_fields.append(f'volumeInGb: {volume_in_gb}')
    if ports is not None:
        ports = ports.replace(' ', '')
        input_fields.append(f'ports: "{ports}"')
    if docker_args is not None:
        input_fields.append(f'dockerArgs: "{docker_args}"')
    if terminate_after is not None:
        input_fields.append(f'terminateAfter: "{terminate_after}"')
    if stop_after is not None:
        input_fields.append(f'stopAfter: "{stop_after}"')
    if data_center_id is not None:
        input_fields.append(f'dataCenterId: "{data_center_id}"')
    if country_code is not None:
        input_fields.append(f'countryCode: "{country_code}"')
    if network_volume_id is not None:
        input_fields.append(f'networkVolumeId: "{network_volume_id}"')
    if allowed_cuda_versions is not None:
        allowed_cuda_versions_string = ', '.join(
            [f'"{version}"' for version in allowed_cuda_versions])
        input_fields.append(
            f'allowedCudaVersions: [{allowed_cuda_versions_string}]')
    if min_download is not None:
        input_fields.append(f'minDownload: {min_download}')
    if min_upload is not None:
        input_fields.append(f'minUpload: {min_upload}')
    if cuda_version is not None:
        input_fields.append(f'cudaVersion: "{cuda_version}"')
    if template_id is not None:
        input_fields.append(f'templateId: "{template_id}"')
    if volume_key is not None:
        input_fields.append(f'volumeKey: "{volume_key}"')

    if env is not None:
        env_string = ', '.join([
            f'{{ key: "{key}", value: "{value}" }}'
            for key, value in env.items()
        ])
        input_fields.append(f'env: [{env_string}]')

    # Format input fields
    input_string = ', '.join(input_fields)
    return f"""
    mutation {{
      podRentInterruptable(
        input: {{
          {input_string}
        }}
      ) {{
        id
        desiredStatus
        imageName
        env
        machineId
        machine {{
          podHostId
        }}
      }}
    }}
    """
