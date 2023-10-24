import os
from typing import Any, Dict, List, Optional, Tuple

from sky.provision.kubernetes import network_utils

_PATH_PREFIX = "/skypilot/{cluster_name_on_cloud}/{port}"


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
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
        network_utils.create_namespaced_service(
            namespace=provider_config["namespace"],
            service_name=service_name,
            port=int(port),
            selector={"skypilot-cluster": cluster_name_on_cloud},
        )
        network_utils.create_namespaced_ingress(
            namespace=provider_config['namespace'],
            ingress_name=ingress_name,
            path_prefix=path_prefix,
            service_name=service_name,
            service_port=int(port),
        )


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
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
    del ip, provider_config  # Unused.

    http_url, https_url = network_utils.get_base_url("ingress-nginx")
    result = {}
    for port in ports:
        path_prefix = _PATH_PREFIX.format(
            cluster_name_on_cloud=cluster_name_on_cloud, port=port)
        result[port] = os.path.join(http_url,
                                    path_prefix.lstrip('/')), os.path.join(
                                        https_url, path_prefix.lstrip('/'))

    return result
