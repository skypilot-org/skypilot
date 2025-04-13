"""Kubernetes network provisioning utils."""
import os
import time
import typing
from typing import Dict, List, Optional, Tuple, Union

import sky
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.adaptors import kubernetes
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import kubernetes_enums
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import jinja2
    import yaml
else:
    jinja2 = adaptors_common.LazyImport('jinja2')
    yaml = adaptors_common.LazyImport('yaml')

logger = sky_logging.init_logger(__name__)

_INGRESS_TEMPLATE_NAME = 'kubernetes-ingress.yml.j2'
_LOADBALANCER_TEMPLATE_NAME = 'kubernetes-loadbalancer.yml.j2'


def get_port_mode(
        mode_str: Optional[str] = None) -> kubernetes_enums.KubernetesPortMode:
    """Get the port mode from the provider config."""

    curr_kube_config = kubernetes_utils.get_current_kube_config_context_name()
    running_kind = curr_kube_config == kubernetes_utils.KIND_CONTEXT_NAME

    if running_kind:
        # If running in kind (`sky local up`), use ingress mode
        return kubernetes_enums.KubernetesPortMode.INGRESS

    mode_str = mode_str or skypilot_config.get_nested(
        ('kubernetes', 'ports'),
        kubernetes_enums.KubernetesPortMode.LOADBALANCER.value)
    try:
        port_mode = kubernetes_enums.KubernetesPortMode(mode_str)
    except ValueError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(str(e)
                + ' Cluster was setup with invalid port mode.'
                + 'Please check the port_mode in provider config.') \
                from None

    return port_mode


def get_networking_mode(
    mode_str: Optional[str] = None
) -> kubernetes_enums.KubernetesNetworkingMode:
    """Get the networking mode from the provider config."""
    mode_str = mode_str or skypilot_config.get_nested(
        ('kubernetes', 'networking_mode'),
        kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD.value)
    try:
        networking_mode = kubernetes_enums.KubernetesNetworkingMode.from_str(
            mode_str)
    except ValueError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(str(e) +
                             ' Please check: ~/.sky/skyconfig.yaml.') from None
    return networking_mode


def fill_loadbalancer_template(namespace: str, service_name: str,
                               ports: List[int], selector_key: str,
                               selector_value: str) -> Dict:
    template_path = os.path.join(sky.__root_dir__, 'templates',
                                 _LOADBALANCER_TEMPLATE_NAME)
    if not os.path.exists(template_path):
        raise FileNotFoundError(
            f'Template "{_LOADBALANCER_TEMPLATE_NAME}" does not exist.')

    with open(template_path, 'r', encoding='utf-8') as fin:
        template = fin.read()
    annotations = skypilot_config.get_nested(
        ('kubernetes', 'custom_metadata', 'annotations'), {})
    labels = skypilot_config.get_nested(
        ('kubernetes', 'custom_metadata', 'labels'), {})
    j2_template = jinja2.Template(template)
    cont = j2_template.render(
        namespace=namespace,
        service_name=service_name,
        ports=ports,
        selector_key=selector_key,
        selector_value=selector_value,
        annotations=annotations,
        labels=labels,
    )
    content = yaml.safe_load(cont)
    return content


def fill_ingress_template(namespace: str, service_details: List[Tuple[str, int,
                                                                      str]],
                          ingress_name: str, selector_key: str,
                          selector_value: str) -> Dict:
    template_path = os.path.join(sky.__root_dir__, 'templates',
                                 _INGRESS_TEMPLATE_NAME)
    if not os.path.exists(template_path):
        raise FileNotFoundError(
            f'Template "{_INGRESS_TEMPLATE_NAME}" does not exist.')
    with open(template_path, 'r', encoding='utf-8') as fin:
        template = fin.read()
    annotations = skypilot_config.get_nested(
        ('kubernetes', 'custom_metadata', 'annotations'), {})
    labels = skypilot_config.get_nested(
        ('kubernetes', 'custom_metadata', 'labels'), {})
    j2_template = jinja2.Template(template)
    cont = j2_template.render(
        namespace=namespace,
        service_names_and_ports=[{
            'service_name': name,
            'service_port': port,
            'path_prefix': path_prefix
        } for name, port, path_prefix in service_details],
        ingress_name=ingress_name,
        selector_key=selector_key,
        selector_value=selector_value,
        annotations=annotations,
        labels=labels,
    )
    content = yaml.safe_load(cont)

    # Return a dictionary containing both specs
    return {
        'ingress_spec': content['ingress_spec'],
        'services_spec': content['services_spec']
    }


def create_or_replace_namespaced_ingress(
        namespace: str, context: Optional[str], ingress_name: str,
        ingress_spec: Dict[str, Union[str, int]]) -> None:
    """Creates an ingress resource for the specified service."""
    networking_api = kubernetes.networking_api(context)

    try:
        networking_api.read_namespaced_ingress(
            ingress_name, namespace, _request_timeout=kubernetes.API_TIMEOUT)
    except kubernetes.kubernetes.client.ApiException as e:
        if e.status == 404:
            networking_api.create_namespaced_ingress(
                namespace,
                ingress_spec,
                _request_timeout=kubernetes.API_TIMEOUT)
            return
        raise e

    networking_api.replace_namespaced_ingress(
        ingress_name,
        namespace,
        ingress_spec,
        _request_timeout=kubernetes.API_TIMEOUT)


def delete_namespaced_ingress(namespace: str, context: Optional[str],
                              ingress_name: str) -> None:
    """Deletes an ingress resource."""
    networking_api = kubernetes.networking_api(context)
    try:
        networking_api.delete_namespaced_ingress(
            ingress_name, namespace, _request_timeout=kubernetes.API_TIMEOUT)
    except kubernetes.kubernetes.client.ApiException as e:
        if e.status == 404:
            raise exceptions.PortDoesNotExistError(
                f'Port {ingress_name.split("--")[-1]} does not exist.')
        raise e


def create_or_replace_namespaced_service(
        namespace: str, context: Optional[str], service_name: str,
        service_spec: Dict[str, Union[str, int]]) -> None:
    """Creates a service resource for the specified service."""
    core_api = kubernetes.core_api(context)

    try:
        core_api.read_namespaced_service(
            service_name, namespace, _request_timeout=kubernetes.API_TIMEOUT)
    except kubernetes.kubernetes.client.ApiException as e:
        if e.status == 404:
            core_api.create_namespaced_service(
                namespace,
                service_spec,
                _request_timeout=kubernetes.API_TIMEOUT)
            return
        raise e

    core_api.replace_namespaced_service(service_name,
                                        namespace,
                                        service_spec,
                                        _request_timeout=kubernetes.API_TIMEOUT)


def delete_namespaced_service(context: Optional[str], namespace: str,
                              service_name: str) -> None:
    """Deletes a service resource."""
    core_api = kubernetes.core_api(context)

    try:
        core_api.delete_namespaced_service(
            service_name, namespace, _request_timeout=kubernetes.API_TIMEOUT)
    except kubernetes.kubernetes.client.ApiException as e:
        if e.status == 404:
            raise exceptions.PortDoesNotExistError(
                f'Port {service_name.split("--")[-1]} does not exist.')
        raise e


def ingress_controller_exists(context: Optional[str],
                              ingress_class_name: str = 'nginx') -> bool:
    """Checks if an ingress controller exists in the cluster."""
    networking_api = kubernetes.networking_api(context)
    ingress_classes = networking_api.list_ingress_class(
        _request_timeout=kubernetes.API_TIMEOUT).items
    return any(
        map(lambda item: item.metadata.name == ingress_class_name,
            ingress_classes))


def get_ingress_external_ip_and_ports(
    context: Optional[str],
    namespace: str = 'ingress-nginx'
) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
    """Returns external ip and ports for the ingress controller."""
    core_api = kubernetes.core_api(context)
    ingress_services = [
        item for item in core_api.list_namespaced_service(
            namespace, _request_timeout=kubernetes.API_TIMEOUT).items
        if item.metadata.name == 'ingress-nginx-controller'
    ]
    if not ingress_services:
        return (None, None)

    ingress_service = ingress_services[0]
    if ingress_service.status.load_balancer.ingress is None:
        # We try to get an IP/host for the service in the following order:
        # 1. Try to use assigned external IP if it exists
        # 2. Use the skypilot.co/external-ip annotation in the service
        # 3. Otherwise return 'localhost'
        ip = None
        if ingress_service.spec.external_i_ps is not None:
            ip = ingress_service.spec.external_i_ps[0]
        elif ingress_service.metadata.annotations is not None:
            ip = ingress_service.metadata.annotations.get(
                'skypilot.co/external-ip', None)
        if ip is None:
            ip = 'localhost'
        ports = ingress_service.spec.ports
        http_port = [port for port in ports if port.name == 'http'][0].node_port
        https_port = [port for port in ports if port.name == 'https'
                     ][0].node_port
        return ip, (int(http_port), int(https_port))

    external_ip = ingress_service.status.load_balancer.ingress[
        0].ip or ingress_service.status.load_balancer.ingress[0].hostname
    return external_ip, None


def get_loadbalancer_ip(context: Optional[str],
                        namespace: str,
                        service_name: str,
                        timeout: int = 0) -> Optional[str]:
    """Returns the IP address of the load balancer."""
    core_api = kubernetes.core_api(context)

    ip = None

    start_time = time.time()
    retry_cnt = 0
    while ip is None and (retry_cnt == 0 or time.time() - start_time < timeout):
        service = core_api.read_namespaced_service(
            service_name, namespace, _request_timeout=kubernetes.API_TIMEOUT)
        if service.status.load_balancer.ingress is not None:
            ip = (service.status.load_balancer.ingress[0].ip or
                  service.status.load_balancer.ingress[0].hostname)
        if ip is None:
            retry_cnt += 1
            if retry_cnt % 5 == 0:
                logger.debug('Waiting for load balancer IP to be assigned'
                             '...')
            time.sleep(1)
    return ip


def get_pod_ip(context: Optional[str], namespace: str,
               pod_name: str) -> Optional[str]:
    """Returns the IP address of the pod."""
    core_api = kubernetes.core_api(context)
    pod = core_api.read_namespaced_pod(pod_name,
                                       namespace,
                                       _request_timeout=kubernetes.API_TIMEOUT)

    return pod.status.pod_ip if pod.status.pod_ip is not None else None
