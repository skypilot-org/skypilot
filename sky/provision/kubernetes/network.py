"""Kubernetes network provisioning."""
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky.adaptors import kubernetes
from sky.provision import common
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import kubernetes_enums
from sky.utils.resources_utils import port_ranges_to_set

logger = sky_logging.init_logger(__name__)

_PATH_PREFIX = '/skypilot/{namespace}/{cluster_name_on_cloud}/{port}'
_LOADBALANCER_SERVICE_NAME = '{cluster_name_on_cloud}--skypilot-lb'


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
    elif port_mode == kubernetes_enums.KubernetesPortMode.PODIP:
        # Do nothing, as PodIP mode does not require opening ports
        pass


def _open_ports_using_loadbalancer(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Dict[str, Any],
) -> None:
    service_name = _LOADBALANCER_SERVICE_NAME.format(
        cluster_name_on_cloud=cluster_name_on_cloud)
    content = network_utils.fill_loadbalancer_template(
        namespace=provider_config.get('namespace', 'default'),
        service_name=service_name,
        ports=ports,
        selector_key='skypilot-cluster',
        selector_value=cluster_name_on_cloud,
    )

    # Update metadata from config
    kubernetes_utils.merge_custom_metadata(content['service_spec']['metadata'])

    network_utils.create_or_replace_namespaced_service(
        namespace=kubernetes_utils.get_namespace_from_config(provider_config),
        context=kubernetes_utils.get_context_from_config(provider_config),
        service_name=service_name,
        service_spec=content['service_spec'])


def _open_ports_using_ingress(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Dict[str, Any],
) -> None:
    context = kubernetes_utils.get_context_from_config(provider_config)
    # Check if an ingress controller exists
    if not network_utils.ingress_controller_exists(context):
        raise Exception(
            'Ingress controller not found. '
            'Install Nginx ingress controller first: '
            'https://github.com/kubernetes/ingress-nginx/blob/main/docs/deploy/index.md.'  # pylint: disable=line-too-long
        )

    # Prepare service names, ports,  for template rendering
    service_details = [
        (f'{cluster_name_on_cloud}--skypilot-svc--{port}', port,
         _PATH_PREFIX.format(
             cluster_name_on_cloud=cluster_name_on_cloud,
             port=port,
             namespace=kubernetes_utils.get_kube_config_context_namespace(
                 context)).rstrip('/').lstrip('/')) for port in ports
    ]

    # Generate ingress and services specs
    # We batch ingress rule creation because each rule triggers a hot reload of
    # the nginx controller. If the ingress rules are created sequentially,
    # it could lead to multiple reloads of the Nginx-Ingress-Controller within
    # a brief period. Consequently, the Nginx-Controller pod might spawn an
    # excessive number of sub-processes. This surge triggers Kubernetes to kill
    # and restart the Nginx due to the podPidsLimit parameter, which is
    # typically set to a default value like 1024.
    # To avoid this, we change ingress creation into one object containing
    # multiple rules.
    content = network_utils.fill_ingress_template(
        namespace=provider_config.get('namespace', 'default'),
        service_details=service_details,
        ingress_name=f'{cluster_name_on_cloud}-skypilot-ingress',
        selector_key='skypilot-cluster',
        selector_value=cluster_name_on_cloud,
    )

    # Create or update services based on the generated specs
    for service_name, service_spec in content['services_spec'].items():
        # Update metadata from config
        kubernetes_utils.merge_custom_metadata(service_spec['metadata'])
        network_utils.create_or_replace_namespaced_service(
            namespace=kubernetes_utils.get_namespace_from_config(
                provider_config),
            context=kubernetes_utils.get_context_from_config(provider_config),
            service_name=service_name,
            service_spec=service_spec,
        )

    kubernetes_utils.merge_custom_metadata(content['ingress_spec']['metadata'])
    # Create or update the single ingress for all services
    network_utils.create_or_replace_namespaced_ingress(
        namespace=kubernetes_utils.get_namespace_from_config(provider_config),
        context=kubernetes_utils.get_context_from_config(provider_config),
        ingress_name=f'{cluster_name_on_cloud}-skypilot-ingress',
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
    elif port_mode == kubernetes_enums.KubernetesPortMode.PODIP:
        # Do nothing, as PodIP mode does not require opening ports
        pass


def _cleanup_ports_for_loadbalancer(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
) -> None:
    service_name = _LOADBALANCER_SERVICE_NAME.format(
        cluster_name_on_cloud=cluster_name_on_cloud)
    network_utils.delete_namespaced_service(
        namespace=provider_config.get('namespace', 'default'),
        service_name=service_name,
    )


def _cleanup_ports_for_ingress(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Dict[str, Any],
) -> None:
    # Delete services for each port
    for port in ports:
        service_name = f'{cluster_name_on_cloud}--skypilot-svc--{port}'
        network_utils.delete_namespaced_service(
            namespace=provider_config.get('namespace',
                                          kubernetes_utils.DEFAULT_NAMESPACE),
            service_name=service_name,
        )

    # Delete the single ingress used for all ports
    ingress_name = f'{cluster_name_on_cloud}-skypilot-ingress'
    network_utils.delete_namespaced_ingress(
        namespace=kubernetes_utils.get_namespace_from_config(provider_config),
        context=kubernetes_utils.get_context_from_config(provider_config),
        ingress_name=ingress_name,
    )


def query_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    head_ip: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[int, List[common.Endpoint]]:
    """See sky/provision/__init__.py"""
    del head_ip  # unused
    assert provider_config is not None, 'provider_config is required'
    port_mode = network_utils.get_port_mode(
        provider_config.get('port_mode', None))
    ports = list(port_ranges_to_set(ports))

    try:
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
                provider_config=provider_config,
            )
        elif port_mode == kubernetes_enums.KubernetesPortMode.PODIP:
            return _query_ports_for_podip(
                cluster_name_on_cloud=cluster_name_on_cloud,
                ports=ports,
                provider_config=provider_config,
            )
        else:
            return {}
    except kubernetes.kubernetes.client.ApiException as e:
        if e.status == 404:
            return {}
        raise e


def _query_ports_for_loadbalancer(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Dict[str, Any],
) -> Dict[int, List[common.Endpoint]]:
    logger.debug(f'Getting loadbalancer IP for cluster {cluster_name_on_cloud}')
    result: Dict[int, List[common.Endpoint]] = {}
    service_name = _LOADBALANCER_SERVICE_NAME.format(
        cluster_name_on_cloud=cluster_name_on_cloud)
    context = provider_config.get(
        'context', kubernetes_utils.get_current_kube_config_context_name())
    namespace = provider_config.get(
        'namespace',
        kubernetes_utils.get_kube_config_context_namespace(context))
    external_ip = network_utils.get_loadbalancer_ip(
        context=context,
        namespace=namespace,
        service_name=service_name,
        # Timeout is set so that we can retry the query when the
        # cluster is firstly created and the load balancer is not ready yet.
        timeout=60,
    )

    if external_ip is None:
        return {}

    for port in ports:
        result[port] = [common.SocketEndpoint(host=external_ip, port=port)]

    return result


def _query_ports_for_ingress(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Dict[str, Any],
) -> Dict[int, List[common.Endpoint]]:
    context = provider_config.get(
        'context', kubernetes_utils.get_current_kube_config_context_name())
    ingress_details = network_utils.get_ingress_external_ip_and_ports(context)
    external_ip, external_ports = ingress_details
    if external_ip is None:
        return {}

    namespace = provider_config.get(
        'namespace',
        kubernetes_utils.get_kube_config_context_namespace(context))
    result: Dict[int, List[common.Endpoint]] = {}
    for port in ports:
        path_prefix = _PATH_PREFIX.format(
            cluster_name_on_cloud=cluster_name_on_cloud,
            port=port,
            namespace=namespace)

        http_port, https_port = external_ports \
            if external_ports is not None else (None, None)
        result[port] = [
            common.HTTPEndpoint(host=external_ip,
                                port=http_port,
                                path=path_prefix.lstrip('/')),
            common.HTTPSEndpoint(host=external_ip,
                                 port=https_port,
                                 path=path_prefix.lstrip('/')),
        ]

    return result


def _query_ports_for_podip(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Dict[str, Any],
) -> Dict[int, List[common.Endpoint]]:
    context = provider_config.get(
        'context', kubernetes_utils.get_current_kube_config_context_name())
    namespace = provider_config.get(
        'namespace',
        kubernetes_utils.get_kube_config_context_namespace(context))
    pod_name = kubernetes_utils.get_head_pod_name(cluster_name_on_cloud)
    pod_ip = network_utils.get_pod_ip(context, namespace, pod_name)

    result: Dict[int, List[common.Endpoint]] = {}
    if pod_ip is None:
        return {}

    for port in ports:
        result[port] = [common.SocketEndpoint(host=pod_ip, port=port)]

    return result
