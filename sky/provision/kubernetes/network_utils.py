import enum
import os
from typing import Dict, List, Tuple, Union

import jinja2
import yaml

import sky
from sky import exceptions
from sky.adaptors import kubernetes

_INGRESS_TEMPLATE_NAME = 'kubernetes-ingress.yml.j2'
_LOADBALANCER_TEMPLATE_NAME = 'kubernetes-loadbalancer.yml.j2'


def fill_loadbalancer_template(namespace: str, service_name: str,
                               ports: List[int], selector_key: str,
                               selector_value: str) -> Dict:
    template_path = os.path.join(sky.__root_dir__, 'templates',
                                 _LOADBALANCER_TEMPLATE_NAME)
    if not os.path.exists(template_path):
        raise FileNotFoundError(
            f'Template "{_LOADBALANCER_TEMPLATE_NAME}" does not exist.')

    with open(template_path) as fin:
        template = fin.read()
    j2_template = jinja2.Template(template)
    cont = j2_template.render(
        namespace=namespace,
        service_name=service_name,
        ports=ports,
        selector_key=selector_key,
        selector_value=selector_value,
    )
    content = yaml.safe_load(cont)
    return content


def fill_ingress_template(namespace: str, path_prefix: str, service_name: str,
                          service_port: int, ingress_name: str,
                          selector_key: str, selector_value: str) -> Dict:
    template_path = os.path.join(sky.__root_dir__, 'templates',
                                 _INGRESS_TEMPLATE_NAME)
    if not os.path.exists(template_path):
        raise FileNotFoundError(
            f'Template "{_INGRESS_TEMPLATE_NAME}" does not exist.')
    with open(template_path) as fin:
        template = fin.read()
    j2_template = jinja2.Template(template)
    cont = j2_template.render(
        namespace=namespace,
        path_prefix=path_prefix.rstrip('/').lstrip('/'),
        service_name=service_name,
        service_port=service_port,
        ingress_name=ingress_name,
        selector_key=selector_key,
        selector_value=selector_value,
    )
    content = yaml.safe_load(cont)
    return content


def create_or_replace_namespaced_ingress(
        namespace: str, ingress_name: str,
        ingress_spec: Dict[str, Union[str, int]]) -> None:
    """Creates an ingress resource for the specified service."""
    networking_api = kubernetes.networking_api()

    try:
        networking_api.read_namespaced_ingress(ingress_name, namespace)
    except kubernetes.get_kubernetes().client.ApiException as e:
        if e.status == 404:
            networking_api.create_namespaced_ingress(namespace, ingress_spec)
            return
        raise e

    networking_api.replace_namespaced_ingress(ingress_name, namespace,
                                              ingress_spec)


def delete_namespaced_ingress(namespace: str, ingress_name: str) -> None:
    """Deletes an ingress resource."""
    networking_api = kubernetes.networking_api()
    try:
        networking_api.delete_namespaced_ingress(ingress_name, namespace)
    except kubernetes.get_kubernetes().client.ApiException as e:
        if e.status == 404:
            raise exceptions.PortDoesNotExistError(
                f'Port {ingress_name.split("--")[-1]} does not exist.')
        raise e


def create_or_replace_namespaced_service(
        namespace: str, service_name: str,
        service_spec: Dict[str, Union[str, int]]) -> None:
    """Creates a service resource for the specified service."""
    core_api = kubernetes.core_api()

    try:
        core_api.read_namespaced_service(service_name, namespace)
    except kubernetes.get_kubernetes().client.ApiException as e:
        if e.status == 404:
            core_api.create_namespaced_service(namespace, service_spec)
            return
        raise e

    core_api.replace_namespaced_service(service_name, namespace, service_spec)


def delete_namespaced_service(namespace: str, service_name: str) -> None:
    """Deletes a service resource."""
    core_api = kubernetes.core_api()

    try:
        core_api.delete_namespaced_service(service_name, namespace)
    except kubernetes.get_kubernetes().client.ApiException as e:
        if e.status == 404:
            raise exceptions.PortDoesNotExistError(
                f'Port {service_name.split("--")[-1]} does not exist.')
        raise e


def ingress_controller_exists(ingress_class_name: str = "nginx") -> bool:
    """Checks if an ingress controller exists in the cluster."""
    networking_api = kubernetes.networking_api()
    ingress_classes = networking_api.list_ingress_class().items
    return any(
        map(lambda item: item.metadata.name == ingress_class_name,
            ingress_classes))


def get_base_url(namespace: str) -> Tuple[str, str]:
    """Returns the HTTP and HTTPS base url of the ingress controller for the cluster."""
    core_api = kubernetes.core_api()
    ingress_service = [
        item for item in core_api.list_namespaced_service(namespace).items
        if item.spec.type == "LoadBalancer"
    ][0]
    if ingress_service.status.load_balancer.ingress is None:
        ports = ingress_service.spec.ports
        http_port = [port for port in ports if port.name == "http"][0].node_port
        https_port = [port for port in ports if port.name == "https"
                     ][0].node_port
        return f'localhost:{http_port}', f'localhost:{https_port}'

    external_ip = ingress_service.status.load_balancer.ingress[0].ip
    return f'http://{external_ip}', f'https://{external_ip}'


def get_loadbalancer_ip(namespace: str, service_name: str) -> str:
    """Returns the IP address of the load balancer."""
    core_api = kubernetes.core_api()
    service = core_api.read_namespaced_service(service_name, namespace)
    return service.status.load_balancer.ingress[0].ip
