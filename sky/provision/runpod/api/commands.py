from typing import Optional, List

from runpod import get_gpu, get_user
from runpod.api.graphql import run_graphql_query

from sky.provision.runpod.api.pods import generate_spot_pod_deployment_mutation


def create_spot_pod(
    name: str,
    image_name: str,
    gpu_type_id: str,
    bid_per_gpu: float,
    cloud_type: str = "ALL",
    gpu_count: int = None,
    min_memory_in_gb: int = None,
    min_vcpu_count: int = None,
    container_disk_in_gb: int = None,
    volume_in_gb: int = None,
    volume_mount_path: str = None,
    ports: str = None,
    start_ssh: bool = True,
    start_jupyter: bool = False,
    env: dict = None,
    docker_args: str = None,
    support_public_ip: bool = True,
    terminate_after: str = None,
    stop_after: str = None,
    data_center_id: str = None,
    country_code: str = None,
    network_volume_id: str = None,
    allowed_cuda_versions: Optional[List[str]] = None,
    min_download: int = None,
    min_upload: int = None,
    cuda_version: str = None,
    template_id: str = None,
    volume_key: str = None,
) -> dict:
    """
    Create a Spot pod.

    Parameters:
        name (str): The name of the Pod.
        image_name (str): The Docker image to use for the Pod environment.
        gpu_type_id (str): The type of GPU required, e.g., "NVIDIA RTX A6000".
        bid_per_gpu (float): The bid price per GPU for the spot instance.
        cloud_type (str, optional): The type of cloud resources, default is "ALL".
        gpu_count (int, optional): The number of GPUs required.
        min_memory_in_gb (int, optional): Minimum memory (in GB) required for the instance.
        min_vcpu_count (int, optional): Minimum number of virtual CPUs required.
        container_disk_in_gb (int, optional): Size of the container disk in GB.
        volume_in_gb (int, optional): Size of the volume in GB.
        volume_mount_path (str, optional): Mount path for the volume, e.g., "/workspace".
        ports (str, optional): Ports to expose, formatted as "port/protocol", e.g., "8888/http".
        start_ssh (bool, optional): Whether to enable SSH access to the Pod. Default is True.
        start_jupyter (bool, optional): Whether to enable Jupyter Notebook in the Pod. Default is False.
        env (dict, optional): Environment variables to set, provided as a dictionary of key-value pairs.
        docker_args (str, optional): Additional Docker runtime arguments for the Pod.
        support_public_ip (bool, optional): Whether to support public IP for the Pod. Default is True.
        terminate_after (str, optional): Time limit after which the Pod will automatically terminate, e.g., "1h".
        stop_after (str, optional): Time limit after which the Pod will automatically stop, e.g., "1h".
        data_center_id (str, optional): Specific data center ID to target for deployment.
        country_code (str, optional): Country code for regional targeting of deployment.
        network_volume_id (str, optional): ID of the network volume to attach.
        allowed_cuda_versions (List[str], optional): List of compatible CUDA versions for the Pod.
        min_download (int, optional): Minimum network download speed (in Mbps) required.
        min_upload (int, optional): Minimum network upload speed (in Mbps) required.
        cuda_version (str, optional): Preferred CUDA version for the Pod.
        template_id (str, optional): ID of the Pod template to use for deployment.
        volume_key (str, optional): Encryption key for the Pod's attached volume.
    :example:

    >>> pod_id = create_spot_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070", bid_per_gpu=0.3)
    """
    get_gpu(gpu_type_id)
    # refer to https://graphql-spec.runpod.io/#definition-CloudTypeEnum
    if cloud_type not in ["ALL", "COMMUNITY", "SECURE"]:
        raise ValueError("cloud_type must be one of ALL, COMMUNITY or SECURE")

    if network_volume_id and data_center_id is None:
        user_info = get_user()
        for network_volume in user_info["networkVolumes"]:
            if network_volume["id"] == network_volume_id:
                data_center_id = network_volume["dataCenterId"]
                break

    if container_disk_in_gb is None and template_id is None:
        container_disk_in_gb = 10

    raw_response = run_graphql_query(
        generate_spot_pod_deployment_mutation(
            name,
            image_name,
            gpu_type_id,
            bid_per_gpu,
            cloud_type,
            gpu_count,
            min_memory_in_gb,
            min_vcpu_count,
            container_disk_in_gb,
            volume_in_gb,
            volume_mount_path,
            ports,
            start_ssh,
            start_jupyter,
            env,
            docker_args,
            support_public_ip,
            terminate_after,
            stop_after,
            data_center_id,
            country_code,
            network_volume_id,
            allowed_cuda_versions,
            min_download,
            min_upload,
            cuda_version,
            template_id,
            volume_key,
        )
    )

    cleaned_response = raw_response["data"]["PodRentInterruptableInput"]
    return cleaned_response
