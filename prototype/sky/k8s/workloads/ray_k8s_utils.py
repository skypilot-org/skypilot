from typing import List, Dict

from kubernetes.client import V1Deployment, V1Service, V1ServicePort, V1EnvVar, V1EnvVarSource, V1ObjectFieldSelector, \
    V1VolumeMount, V1Volume, V1EmptyDirVolumeSource, V1ResourceFieldSelector, V1ResourceRequirements

from sky.k8s.workloads.k8s_utils import get_template_deployment, get_template_service

RAY_HEAD_PORTS = {
    "client": 10001,
    "dashboard": 8265,
    "redis": 6379,
}


def convert_dictargs_to_listargs(dict_args: Dict):
    # Converts dictionary args to sequential args for cmd line
    listargs = []
    for k, v in dict_args.items():
        listargs.append(k)
        listargs.append(v)
    return listargs


# ==========================================================
# =================== RAY HEAD OBJECTS =====================
# ==========================================================

def get_ray_head_template_deployment(app_name: str,
                                     container_image: str = None) -> V1Deployment:
    """
    Defines the ray head node. Does not get autoscaled with more resources - only 1 head is needed.
    Also not counted as a workload, thus is_workload is false.
    :param app_name: Name of the application
    :param container_image: Ray image to use
    :return: V1Deployment object
    """
    # Assign defaults:
    app_name = app_name if app_name else "default"
    head_name = app_name + "-head"
    default_replicas = 1
    container_image = container_image if container_image else "public.ecr.aws/cilantro/cray-workloads:latest"
    container_ports = list(RAY_HEAD_PORTS.values())
    container_image_pull_policy = "Always"
    container_command = ["/bin/bash", "-c", "--"]
    container_args = [
        "ray start --head --port=6379 --redis-shard-ports=6380,6381 --num-cpus=0 --object-manager-port=12345 --node-manager-port=12346 --dashboard-host=0.0.0.0 --block"]
    envs = [V1EnvVar("POD_IP", value_from=V1EnvVarSource(
        field_ref=V1ObjectFieldSelector(field_path="status.podIP"))),
            V1EnvVar("MY_CPU_REQUEST", value_from=V1EnvVarSource(
                resource_field_ref=V1ResourceFieldSelector(resource="requests.cpu")))
            ]

    head_deployment = get_template_deployment(app_name=head_name,
                                              default_replicas=default_replicas,
                                              container_image=container_image,
                                              container_ports=container_ports,
                                              container_image_pull_policy=container_image_pull_policy,
                                              container_command=container_command,
                                              container_args=container_args)

    # All updates we make here happen in place - no need to create new V1DeploymentObject
    podspec = head_deployment.spec.template.spec

    # Add dshm volume
    dshm_volume = V1Volume(empty_dir=V1EmptyDirVolumeSource(medium="Memory"), name="dshm")
    podspec.volumes = [dshm_volume]
    dshm_mount = V1VolumeMount(mount_path="/dev/shm", name="dshm")

    # Set resource requirements
    resreq = V1ResourceRequirements(requests={"cpu": "100m",
                                              "memory": "512m"})

    container = podspec.containers[0]
    container.volume_mounts = [dshm_mount]
    container.resources = resreq

    # Update environment variables
    current_envs = container.env
    if not current_envs:
        container.env = envs
    else:
        current_envs.extend(envs)

    return head_deployment


def get_ray_head_template_service(app_name: str,
                                  meta_labels: Dict[str, str] = None) -> V1Service:
    """
    Defines a headless service that allows for head connections.
    :param app_name: Name of the application in the hierarchy. DO NOT append anything before passing it to this method.
    :return: V1Service object
    """
    # Assign defaults:
    app_name = app_name if app_name else "default"
    head_name = app_name + "-head"
    meta_labels = meta_labels if meta_labels else {}
    meta_labels["app"] = app_name
    ports = [V1ServicePort(name=n, port=p) for n, p in RAY_HEAD_PORTS.items()]
    selector_match_labels = {"app": head_name}

    head_svc = get_template_service(svc_name=head_name + "-svc",
                                    meta_labels=meta_labels,
                                    ports=ports,
                                    selector_match_labels=selector_match_labels)

    # Make it a "headless" service
    # head_svc.spec.cluster_ip = "None"

    return head_svc


# ==========================================================
# =========== WORKER (Server) OBJECTS ======================
# ==========================================================

def get_ray_wroker_template_deployment(app_name: str,
                                       head_svc_name: str,
                                       container_image: str = None) -> V1Deployment:
    """
    Defines the ray worker objects. This is the k8s deployment that gets scaled with more resources.
    :param app_name: Name of the application in the hierarchy. DO NOT append anything before passing it to this method.
    :return: V1Deployment object
    """
    # Assign defaults:
    app_name = app_name if app_name else "default"
    is_workload = "true"
    default_replicas = 1
    container_image = container_image if container_image else "public.ecr.aws/cilantro/cray-workloads:latest"
    container_ports = list(RAY_HEAD_PORTS.values())
    container_image_pull_policy = "Always"
    container_command = ["/bin/bash", "-c", "--"]
    head_svc_envvar_name_prefix = head_svc_name.replace("-", "_").upper()
    container_args = [
        f"ray start --num-cpus=$MY_CPU_REQUEST --address=${head_svc_envvar_name_prefix}_SERVICE_HOST:${head_svc_envvar_name_prefix}_SERVICE_PORT_REDIS --object-manager-port=12345 --node-manager-port=12346 --block"]
    server_envs = [
        V1EnvVar("POD_IP", value_from=V1EnvVarSource(
            field_ref=V1ObjectFieldSelector(field_path="status.podIP"))),
        V1EnvVar("MY_CPU_REQUEST", value_from=V1EnvVarSource(
            resource_field_ref=V1ResourceFieldSelector(resource="requests.cpu")))
    ]

    server_deployment = get_template_deployment(app_name=app_name,
                                                is_workload=is_workload,
                                                default_replicas=default_replicas,
                                                container_image=container_image,
                                                container_ports=container_ports,
                                                container_image_pull_policy=container_image_pull_policy,
                                                container_command=container_command,
                                                container_args=container_args)

    # All updates we make here happen in place - no need to create new V1DeploymentObject
    podspec = server_deployment.spec.template.spec

    # Add dshm volume
    dshm_volume = V1Volume(empty_dir=V1EmptyDirVolumeSource(medium="Memory"), name="dshm")
    podspec.volumes = [dshm_volume]
    dshm_mount = V1VolumeMount(mount_path="/dev/shm", name="dshm")

    # Set resource requirements
    resreq = V1ResourceRequirements(requests={"cpu": "100m",
                                              "memory": "512m"})

    container = podspec.containers[0]
    container.volume_mounts = [dshm_mount]
    container.resources = resreq

    # Update environment variables
    current_envs = container.env
    if not current_envs:
        container.env = server_envs
    else:
        current_envs.extend(server_envs)

    return server_deployment


# ==========================================================
# ================= CLIENT OBJECTS =========================
# ==========================================================

def get_cilantro_template_deployment(app_name: str,
                                     workload_run_cmd: List[str],
                                     workload_args: List[str, str],
                                     container_image: str = None) -> V1Deployment:
    """
    Defines the deployment for the cray client.
    A pod contains two containers - cray client and cilantro client.
    :param app_name: Name of the application
    :return: V1Deployment object
    """
    # Assign defaults:
    app_name = app_name if app_name else "default"
    client_name = app_name + "-client"
    default_replicas = 1
    container_image = container_image if container_image else "public.ecr.aws/cilantro/cray-workloads:latest"
    container_ports = []
    container_image_pull_policy = "Always"

    envs = [V1EnvVar("POD_IP", value_from=V1EnvVarSource(
        field_ref=V1ObjectFieldSelector(field_path="status.podIP")))]

    client_deployment = get_template_deployment(app_name=client_name,
                                                default_replicas=default_replicas,
                                                container_image=container_image,
                                                container_ports=container_ports,
                                                container_image_pull_policy=container_image_pull_policy,
                                                container_command=workload_run_cmd,
                                                container_args=workload_args)

    # Add volume to the pod spec
    podspec = client_deployment.spec.template.spec
    log_volume = V1Volume(empty_dir=V1EmptyDirVolumeSource(), name="log-share")
    podspec.volumes = [log_volume]
    podspec.termination_grace_period_seconds = 0

    # Create volume mount for shared log mount
    log_mount = V1VolumeMount(mount_path="/cilantrologs", name="log-share")

    # All updates we make here happen in place - no need to create new V1DeploymentObject
    crayclient_container = podspec.containers[0]
    crayclient_container.name = "cray"
    crayclient_container.volume_mounts = [log_mount]

    current_envs = crayclient_container.env
    if not current_envs:
        crayclient_container.env = envs
    else:
        current_envs.extend(envs)

    return client_deployment