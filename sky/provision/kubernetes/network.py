import os
from typing import Any, Dict, List, Optional, Tuple

from sky import skypilot_config
from sky.provision.kubernetes import network_utils
from sky.utils import ux_utils

_PATH_PREFIX = "/skypilot/{cluster_name_on_cloud}/{port}"
_LOADBALANCER_SERVICE_NAME = "{cluster_name_on_cloud}-skypilot-loadbalancer"


def _get_port_mode() -> network_utils.KubernetesPortMode:
    mode_str = skypilot_config.get_nested(
        ('kubernetes', 'ports'),
        network_utils.KubernetesPortMode.LOADBALANCER.value)
    try:
        port_mode = network_utils.KubernetesPortMode.from_str(mode_str)
    except ValueError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(str(e) + ' Please check: ~/.sky/config.yaml.') \
                from None

    return port_mode


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    port_mode = _get_port_mode()

    if port_mode == network_utils.KubernetesPortMode.LOADBALANCER:
        _open_ports_using_loadbalancer(
            cluster_name_on_cloud=cluster_name_on_cloud,
            ports=ports,
            provider_config=provider_config)
    elif port_mode == network_utils.KubernetesPortMode.INGRESS:
        _open_ports_using_ingress(cluster_name_on_cloud=cluster_name_on_cloud,
                                  ports=ports,
                                  provider_config=provider_config)


def _open_ports_using_loadbalancer(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    assert provider_config is not None, 'provider_config is required'
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
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    if not network_utils.ingress_controller_exists():
        raise Exception(
            "Ingress controller not found. Please install ingress controller first."
        )

    assert provider_config is not None, 'provider_config is required'
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
    port_mode = _get_port_mode()
    if port_mode == network_utils.KubernetesPortMode.LOADBALANCER:
        _cleanup_ports_for_loadbalancer(
            cluster_name_on_cloud=cluster_name_on_cloud,
            provider_config=provider_config)
    elif port_mode == network_utils.KubernetesPortMode.INGRESS:
        _cleanup_ports_for_ingress(cluster_name_on_cloud=cluster_name_on_cloud,
                                   ports=ports,
                                   provider_config=provider_config)


def _cleanup_ports_for_loadbalancer(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    assert provider_config is not None, cluster_name_on_cloud
    service_name = _LOADBALANCER_SERVICE_NAME.format(
        cluster_name_on_cloud=cluster_name_on_cloud)
    network_utils.delete_namespaced_service(
        namespace=provider_config["namespace"],
        service_name=service_name,
    )


def _cleanup_ports_for_ingress(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    assert provider_config is not None, cluster_name_on_cloud
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
) -> Dict[str, Tuple[str, str]]:
    """See sky/provision/__init__.py"""
    del ip  # Unused.
    port_mode = _get_port_mode()
    if port_mode == network_utils.KubernetesPortMode.LOADBALANCER:
        assert provider_config is not None, 'provider_config is required'
        return _query_ports_for_loadbalancer(
            cluster_name_on_cloud=cluster_name_on_cloud,
            ports=ports,
            provider_config=provider_config,
        )
    elif port_mode == network_utils.KubernetesPortMode.INGRESS:
        return _query_ports_for_ingress(
            cluster_name_on_cloud=cluster_name_on_cloud,
            ports=ports,
        )
    else:
        return {}


def _query_ports_for_loadbalancer(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Dict[str, Any],
) -> Dict[str, Tuple[str, str]]:
    result = {}
    service_name = _LOADBALANCER_SERVICE_NAME.format(
        cluster_name_on_cloud=cluster_name_on_cloud)
    external_ip = network_utils.get_loadbalancer_ip(
        namespace=provider_config['namespace'], service_name=service_name)
    for port in ports:
        result[port] = f"{external_ip}:{port}", f"{external_ip}:{port}"

    return result


def _query_ports_for_ingress(
    cluster_name_on_cloud: str,
    ports: List[str],
) -> Dict[str, Tuple[str, str]]:
    http_url, https_url = network_utils.get_base_url("ingress-nginx")
    result = {}
    for port in ports:
        path_prefix = _PATH_PREFIX.format(
            cluster_name_on_cloud=cluster_name_on_cloud, port=port)
        result[port] = os.path.join(http_url,
                                    path_prefix.lstrip('/')), os.path.join(
                                        https_url, path_prefix.lstrip('/'))

    return result
