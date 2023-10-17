import os
from typing import Any, Dict, List, Optional, Tuple

from sky.provision.kubernetes import network_utils


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],  # pylint: disable=unused-argument
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    if not network_utils.ingress_controller_exists():
        raise Exception(
            "Ingress controller not found. Please install ingress controller first."
        )

    assert provider_config, 'provider_config is required'
    for port_details in provider_config['ports']:
        service_name = port_details['service_name']
        ingress_name = port_details['ingress_name']
        path_prefix = port_details['path_prefix']
        port = port_details['port']
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
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    if 'ports' in provider_config:
        for port_details in provider_config['ports']:
            service_name = port_details['service_name']
            ingress_name = port_details['ingress_name']
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
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Tuple[str, str]]:
    """See sky/provision/__init__.py"""
    if provider_config is None:
        return {}

    http_url, https_url = network_utils.get_base_url("ingress-nginx")
    result = {}
    if 'ports' in provider_config:
        for port_details in provider_config['ports']:
            result[port_details['port']] = os.path.join(
                http_url,
                port_details['path_prefix'].lstrip('/')), os.path.join(
                    https_url, port_details['path_prefix'].lstrip('/'))

    return result
