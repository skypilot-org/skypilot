import logging
from typing import List, Dict, TypeVar

from kubernetes.client import V1Deployment, V1Service, V1ObjectMeta, V1DeploymentSpec, V1PodTemplateSpec, \
    V1LabelSelector, V1PodSpec, V1Container, V1ContainerPort, V1ServiceSpec, V1ServicePort, V1LabelSelectorRequirement

logger = logging.getLogger(__name__)

def get_template_deployment(app_name: str = "default",
                            is_workload: str = "false",
                            threshold: str = "",
                            app_weight: str = "",
                            app_unit_demand: str = "",
                            default_replicas: int = 1,
                            container_image: str = "nginx:1.15.4",
                            container_ports: List[int] = None,
                            container_image_pull_policy: str = "Always",
                            container_command: List[str] = None,
                            container_args: List[str] = None,
                            override_labels: Dict[str, str] = None,
                            *args, **kwargs
                            ) -> V1Deployment:
    """
    Defines the deployment template populated with fields from the workload_info in leaf nodes.
    :return: V1Deployment object
    """
    # Assign defaults:
    app_name = app_name if app_name else "default"
    is_workload = is_workload if is_workload else "false"
    threshold = threshold if threshold else ""
    app_weight = app_weight if app_weight else app_weight
    app_unit_demand = app_unit_demand if app_unit_demand else ""
    default_replicas = default_replicas if default_replicas else 1
    container_image = container_image if container_image else "nginx:1.15.4"
    container_ports = container_ports if container_ports else [80]
    container_image_pull_policy = container_image_pull_policy if container_image_pull_policy else "Always"
    container_command = container_command if container_command else None
    container_args = container_args if container_args else None  # Default to none.
    override_labels = override_labels if override_labels else {}

    # Generate metadata
    meta_labels = {
        'app': app_name,
        'is_workload': is_workload
    }
    meta_labels.update(override_labels)

    if threshold:
        meta_labels["threshold"] = threshold
    if app_weight:
        meta_labels["app_weight"] = app_weight
    if app_unit_demand:
        meta_labels["app_unit_demand"] = app_unit_demand
    metadata = V1ObjectMeta(name=app_name, labels=meta_labels)

    # Pod template spec
    container = V1Container(name=app_name,
                            image=container_image,
                            ports=[V1ContainerPort(container_port=p) for p in container_ports],
                            image_pull_policy=container_image_pull_policy,
                            command=container_command,
                            args=container_args)
    pod_spec = V1PodSpec(containers=[container], termination_grace_period_seconds=0)
    pod_template = V1PodTemplateSpec(metadata=V1ObjectMeta(labels=meta_labels),
                                     spec=pod_spec)

    # Deployment spec
    spec = V1DeploymentSpec(replicas=default_replicas,
                            selector=V1LabelSelector(match_labels=meta_labels),
                            template=pod_template)

    # Create object
    dep = V1Deployment(metadata=metadata, spec=spec)
    return dep

T = TypeVar('T')

def get_template_service(svc_name: str = "default",
                         meta_labels: Dict[str, str] = None,
                         ports: List[V1ServicePort] = None,
                         selector_match_labels: Dict[str, str] = None,
                         ) -> V1Service:
    """
    Defines the service template populated with fields from the workload_info in leaf nodes.
    :return: V1Service object
    """
    # Assign defaults:
    svc_name = svc_name if svc_name else "default"
    meta_labels = meta_labels if meta_labels else {}
    selector_match_labels = selector_match_labels
    if not selector_match_labels:
        logger.warning(f"selector_match_labels not specified for service {svc_name}! This service does nothing...")

    metadata = V1ObjectMeta(name=svc_name, labels=meta_labels)

    spec = V1ServiceSpec(selector=selector_match_labels, ports=ports)

    # Create object
    dep = V1Service(metadata=metadata, spec=spec)
    return dep