import os
from typing import Any, Dict, List, Optional

from sky.provision import common
from sky.provision.kubernetes import network_utils
from sky.utils import kubernetes_enums
from sky.utils.resources_utils import port_ranges_to_set

_PATH_PREFIX = '/skypilot/{cluster_name_on_cloud}/{port}'
_LOADBALANCER_SERVICE_NAME = '{cluster_name_on_cloud}-skypilot-loadbalancer'


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, 'provider_config is required'
    port_mode = network_utils.get_port_mode(
        provider_config.get('port_mode', None))
    ports = list(port_ranges_to_set(ports))
    if port_mode == kubernetes_enums.KubernetesPortMode.LOADBALANCER:
        _open_ports_using_loadbalancer(
            cluster_name_on_cloud=cluster_name_on_cloud,
            ports=ports,
            provider_config=provider_config)
    elif port_mode == kubernetes_enums.KubernetesPortMode.INGRESS:
        _open_ports_using_ingress(cluster_name_on_cloud=cluster_name_on_cloud,
                                  ports=ports,
                                  provider_config=provider_config)


def _open_ports_using_loadbalancer(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Dict[str, Any],
) -> None:
    service_name = _LOADBALANCER_SERVICE_NAME.format(
        cluster_name_on_cloud=cluster_name_on_cloud)
    content = network_utils.fill_loadbalancer_template(
        namespace=provider_config['namespace'],
        service_name=service_name,
        ports=ports,
        selector_key='skypilot-cluster',
        selector_value=cluster_name_on_cloud,
    )
    network_utils.create_or_replace_namespaced_service(
        namespace=provider_config['namespace'],
        service_name=service_name,
        service_spec=content['service_spec'])


def _open_ports_using_ingress(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Dict[str, Any],
) -> None:
    if not network_utils.ingress_controller_exists():
        raise Exception(
            "Ingress controller not found. Please install ingress controller first."
        )

    for port in ports:
        service_name = f"{cluster_name_on_cloud}-skypilot-service--{port}"
        ingress_name = f"{cluster_name_on_cloud}-skypilot-ingress--{port}"
        path_prefix = _PATH_PREFIX.format(
            cluster_name_on_cloud=cluster_name_on_cloud, port=port)

        content = network_utils.fill_ingress_template(
            namespace=provider_config['namespace'],
            path_prefix=path_prefix,
            service_name=service_name,
            service_port=port,
            ingress_name=ingress_name,
            selector_key='skypilot-cluster',
            selector_value=cluster_name_on_cloud,
        )
        network_utils.create_or_replace_namespaced_service(
            namespace=provider_config["namespace"],
            service_name=service_name,
            service_spec=content['service_spec'],
        )
        network_utils.create_or_replace_namespaced_ingress(
            namespace=provider_config['namespace'],
            ingress_name=ingress_name,
            ingress_spec=content['ingress_spec'],
        )


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, 'provider_config is required'
    port_mode = network_utils.get_port_mode(
        provider_config.get('port_mode', None))
    ports = list(port_ranges_to_set(ports))
    if port_mode == kubernetes_enums.KubernetesPortMode.LOADBALANCER:
        _cleanup_ports_for_loadbalancer(
            cluster_name_on_cloud=cluster_name_on_cloud,
            provider_config=provider_config)
    elif port_mode == kubernetes_enums.KubernetesPortMode.INGRESS:
        _cleanup_ports_for_ingress(cluster_name_on_cloud=cluster_name_on_cloud,
                                   ports=ports,
                                   provider_config=provider_config)


def _cleanup_ports_for_loadbalancer(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
) -> None:
    service_name = _LOADBALANCER_SERVICE_NAME.format(
        cluster_name_on_cloud=cluster_name_on_cloud)
    network_utils.delete_namespaced_service(
        namespace=provider_config["namespace"],
        service_name=service_name,
    )


def _cleanup_ports_for_ingress(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Dict[str, Any],
) -> None:
    for port in ports:
        service_name = f"{cluster_name_on_cloud}-skypilot-service--{port}"
        ingress_name = f"{cluster_name_on_cloud}-skypilot-ingress--{port}"
        network_utils.delete_namespaced_service(
            namespace=provider_config["namespace"],
            service_name=service_name,
        )
        network_utils.delete_namespaced_ingress(
            namespace=provider_config['namespace'],
            ingress_name=ingress_name,
        )


def query_ports(
    cluster_name_on_cloud: str,
    ip: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[int, List[common.Endpoint]]:
    """See sky/provision/__init__.py"""
    del ip  # Unused.
    assert provider_config is not None, 'provider_config is required'
    port_mode = network_utils.get_port_mode(
        provider_config.get('port_mode', None))
    ports = list(port_ranges_to_set(ports))
    if port_mode == kubernetes_enums.KubernetesPortMode.LOADBALANCER:
        return _query_ports_for_loadbalancer(
            cluster_name_on_cloud=cluster_name_on_cloud,
            ports=ports,
            provider_config=provider_config,
        )
    elif port_mode == kubernetes_enums.KubernetesPortMode.INGRESS:
        return _query_ports_for_ingress(
            cluster_name_on_cloud=cluster_name_on_cloud,
            ports=ports,
        )
    else:
        return {}


def _query_ports_for_loadbalancer(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Dict[str, Any],
) -> Dict[int, List[common.Endpoint]]:
    result: Dict[int, List[common.Endpoint]] = {}
    service_name = _LOADBALANCER_SERVICE_NAME.format(
        cluster_name_on_cloud=cluster_name_on_cloud)
    external_ip = network_utils.get_loadbalancer_ip(
        namespace=provider_config['namespace'], service_name=service_name)

    if external_ip is None:
        return {}

    for port in ports:
        result[port] = [common.SocketEndpoint(host=external_ip, port=port)]

    return result


def _query_ports_for_ingress(
    cluster_name_on_cloud: str,
    ports: List[int],
) -> Dict[int, List[common.Endpoint]]:
    url_details = network_utils.get_base_url("ingress-nginx")
    if url_details is None:
        return {}

    http_url, https_url = url_details
    result: Dict[int, List[common.Endpoint]] = {}
    for port in ports:
        path_prefix = _PATH_PREFIX.format(
            cluster_name_on_cloud=cluster_name_on_cloud, port=port)
        result[port] = [
            common.HTTPEndpoint(
                url=os.path.join(http_url, path_prefix.lstrip('/'))),
            common.HTTPEndpoint(
                url=os.path.join(https_url, path_prefix.lstrip('/'))),
        ]

    return result
