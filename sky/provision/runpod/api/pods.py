from typing import List, Optional

"""
This module provides functions to generate GraphQL mutations for deploying
spot instance Pods on RunPod.
"""

# fields defined https://graphql-spec.runpod.io/#definition-PodRentInterruptableInput
def generate_spot_pod_deployment_mutation(
    name: str,
    image_name: str,
    gpu_type_id: str,
    bid_per_gpu: float,
    cloud_type: str = 'ALL',
    gpu_count: Optional[int] = None,
    min_memory_in_gb: Optional[int] = None,
    min_vcpu_count: Optional[int] = None,
    container_disk_in_gb: Optional[int] = None,
    volume_in_gb: Optional[int] = None,
    volume_mount_path: Optional[str] = None,
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
    """
    Generate a GraphQL mutation for deploying a spot instance Pod on RunPod.

    Parameters:
        name (str): The name of the Pod.
        image_name (str): The Docker image to use for the Pod environment.
        gpu_type_id (str): The type of GPU required, e.g., 'NVIDIA RTX A6000'.
        bid_per_gpu (float): The bid price per GPU for the spot instance.
        cloud_type (str, optional): The type of cloud resources, default is 'ALL'.
        gpu_count (int, optional): The number of GPUs required.
        min_memory_in_gb (int, optional): Minimum memory (in GB) required for the instance.
        min_vcpu_count (int, optional): Minimum number of virtual CPUs required.
        container_disk_in_gb (int, optional): Size of the container disk in GB.
        volume_in_gb (int, optional): Size of the volume in GB.
        volume_mount_path (str, optional): Mount path for the volume, e.g., '/workspace'.
        ports (str, optional): Ports to expose, formatted as 'port/protocol', e.g., '8888/http'.
        start_ssh (bool, optional): Whether to enable SSH access to the Pod. Default is True.
        start_jupyter (bool, optional): Whether to enable Jupyter Notebook in the Pod. Default is False.
        env (dict, optional): Environment variables to set, provided as a dictionary of key-value pairs.
        docker_args (str, optional): Additional Docker runtime arguments for the Pod.
        support_public_ip (bool, optional): Whether to support public IP for the Pod. Default is True.
        terminate_after (str, optional): Time limit after which the Pod will automatically terminate, e.g., '1h'.
        stop_after (str, optional): Time limit after which the Pod will automatically stop, e.g., '1h'.
        data_center_id (str, optional): Specific data center ID to target for deployment.
        country_code (str, optional): Country code for regional targeting of deployment.
        network_volume_id (str, optional): ID of the network volume to attach.
        allowed_cuda_versions (List[str], optional): List of compatible CUDA versions for the Pod.
        min_download (int, optional): Minimum network download speed (in Mbps) required.
        min_upload (int, optional): Minimum network upload speed (in Mbps) required.
        cuda_version (str, optional): Preferred CUDA version for the Pod.
        template_id (str, optional): ID of the Pod template to use for deployment.
        volume_key (str, optional): Encryption key for the Pod's attached volume.

    Returns:
        str: The formatted GraphQL mutation string for deploying a spot instance Pod.
    """
    input_fields = []

    # ----------------------------- Required Fields ----------------------------- #
    input_fields.append(f'name: \'{name}\'')
    input_fields.append(f'imageName: \'{image_name}\'')
    input_fields.append(f'gpuTypeId: \'{gpu_type_id}\'')
    input_fields.append(f'bidPerGpu: {bid_per_gpu}')

    # ----------------------------- Default Fields ------------------------------ #
    input_fields.append(f'cloudType: {cloud_type}')

    if start_ssh:
        input_fields.append('startSsh: true')
    if start_jupyter:
        input_fields.append('startJupyter: true')
    if support_public_ip:
        input_fields.append('supportPublicIp: true')
    else:
        input_fields.append('supportPublicIp: false')

    # ----------------------------- Optional Fields ----------------------------- #
    optional_fields = {
        'gpuCount': gpu_count,
        'minMemoryInGb': min_memory_in_gb,
        'minVcpuCount': min_vcpu_count,
        'containerDiskInGb': container_disk_in_gb,
        'volumeInGb': volume_in_gb,
        'volumeMountPath': volume_mount_path,
        'ports': ports,
        'dockerArgs': docker_args,
        'terminateAfter': terminate_after,
        'stopAfter': stop_after,
        'dataCenterId': data_center_id,
        'countryCode': country_code,
        'networkVolumeId': network_volume_id,
        'minDownload': min_download,
        'minUpload': min_upload,
        'cudaVersion': cuda_version,
        'templateId': template_id,
        'volumeKey': volume_key,
    }

    for key, value in optional_fields.items():
        if value is not None:
            input_fields.append(
                f'{key}: \'{value}\'' if isinstance(value, str) else f'{key}: {value}'
            )

    if env is not None:
        env_string = ', '.join(
            [f'{{ key: \'{key}\', value: \'{value}\' }}' for key, value in env.items()]
        )
        input_fields.append(f'env: [{env_string}]')

    if allowed_cuda_versions is not None:
        allowed_cuda_versions_string = ', '.join(
            [f'\'{version}\'' for version in allowed_cuda_versions]
        )
        input_fields.append(f'allowedCudaVersions: [{allowed_cuda_versions_string}]')

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
