from typing import Dict, Tuple

from sky import exceptions
from sky.adaptors import kubernetes


def create_namespaced_ingress(namespace: str, ingress_name: str,
                              path_prefix: str, service_name: str,
                              service_port: int) -> None:
    """Creates an ingress resource for the specified service."""
    client = kubernetes.get_kubernetes().client
    body = client.V1Ingress(
        api_version="networking.k8s.io/v1",
        kind="Ingress",
        metadata=client.V1ObjectMeta(
            name=ingress_name,
            annotations={
                "nginx.ingress.kubernetes.io/use-regex": "true",
                "nginx.ingress.kubernetes.io/rewrite-target": "/$2"
            }),
        spec=client.V1IngressSpec(
            ingress_class_name="nginx",
            rules=[
                client.V1IngressRule(http=client.V1HTTPIngressRuleValue(paths=[
                    client.V1HTTPIngressPath(
                        path=f'/{path_prefix.rstrip("/").lstrip("/")}(/|$)(.*)',
                        path_type="ImplementationSpecific",
                        backend=client.V1IngressBackend(
                            service=client.V1IngressServiceBackend(
                                port=client.V1ServiceBackendPort(
                                    number=service_port,),
                                name=service_name)))
                ]))
            ]))

    networking_api = kubernetes.networking_api()
    networking_api.create_namespaced_ingress(namespace, body)


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


def create_namespaced_service(namespace: str, service_name: str, port: int,
                              selector: Dict[str, str]) -> None:
    """Creates a service resource for the specified service."""
    client = kubernetes.get_kubernetes().client
    body = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(name=service_name,),
        spec=client.V1ServiceSpec(
            selector=selector,
            ports=[client.V1ServicePort(port=port, target_port=port)]))

    core_api = kubernetes.core_api()
    core_api.create_namespaced_service(namespace, body)


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
