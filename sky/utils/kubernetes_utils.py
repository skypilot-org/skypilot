"""Kubernetes utilities for SkyPilot."""
import enum
import os
from typing import Dict, Optional, Set, Tuple
from urllib.parse import urlparse

import jinja2
import yaml

import sky
from sky import sky_logging
from sky.adaptors import kubernetes
from sky.backends import backend_utils
from sky.utils import common_utils

DEFAULT_NAMESPACE = 'default'
LOCAL_PORT_FOR_PORT_FORWARD = 23100

logger = sky_logging.init_logger(__name__)


class KubernetesNetworkingMode(enum.Enum):
    NODEPORT = 'NODEPORT'
    PORT_FORWARD = 'PORT_FORWARD'


class GPULabelFormatter:
    """Base class to define a GPU label formatter for a Kubernetes cluster

    A GPU label formatter is a class that defines how to use GPU type labels in
    a Kubernetes cluster. It is used by the Kubernetes cloud class to pick the
    key:value pair to use as node selector for GPU nodes.
    """

    @classmethod
    def get_label_key(cls) -> str:
        """Returns the label key for GPU type used by the Kubernetes cluster"""
        raise NotImplementedError

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        """Given a GPU type, returns the label value to be used"""
        raise NotImplementedError


def get_gke_accelerator_name(accelerator: str) -> str:
    """Returns the accelerator name for GKE clusters

    Uses the format - nvidia-tesla-<accelerator>.
    A100-80GB and L4 are an exception - they use nvidia-<accelerator>.
    """
    if accelerator in ('A100-80GB', 'L4'):
        # A100-80GB and L4 have a different name pattern.
        return 'nvidia-{}'.format(accelerator.lower())
    else:
        return 'nvidia-tesla-{}'.format(accelerator.lower())


class GKELabelFormatter(GPULabelFormatter):
    """GKE label formatter

    GKE nodes by default are populated with `cloud.google.com/gke-accelerator`
    label, which is used to identify the GPU type.
    """

    LABEL_KEY = 'cloud.google.com/gke-accelerator'

    @classmethod
    def get_label_key(cls) -> str:
        return cls.LABEL_KEY

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        return get_gke_accelerator_name(accelerator)


class SkyPilotLabelFormatter(GPULabelFormatter):
    """Custom label formatter for SkyPilot

    Uses skypilot.co/accelerator as the key, and SkyPilot accelerator str as the
    value.
    """

    LABEL_KEY = 'skypilot.co/accelerator'

    @classmethod
    def get_label_key(cls) -> str:
        return cls.LABEL_KEY

    @classmethod
    def get_label_value(cls, accelerator: str) -> str:
        # For SkyPilot formatter, we use the accelerator str directly.
        # See sky.utils.kubernetes.gpu_labeler.
        return accelerator.lower()


# LABEL_FORMATTER_REGISTRY stores the label formats SkyPilot will try to
# discover the accelerator type from. The order of the list is important, as
# it will be used to determine the priority of the label formats.
LABEL_FORMATTER_REGISTRY = [SkyPilotLabelFormatter, GKELabelFormatter]


def detect_gpu_label_formatter(
) -> Tuple[Optional[GPULabelFormatter], Set[str]]:
    # Get the set of labels across all nodes
    # TODO(romilb): This is not efficient. We should cache the node labels
    node_labels: Set[str] = set()
    for node in kubernetes.core_api().list_node().items:
        node_labels.update(node.metadata.labels.keys())

    # Check if the node labels contain any of the GPU label prefixes
    for label_formatter in LABEL_FORMATTER_REGISTRY:
        if label_formatter.get_label_key() in node_labels:
            return label_formatter(), node_labels
    return None, node_labels


def get_head_ssh_port(cluster_name: str, namespace: str) -> int:
    svc_name = f'{cluster_name}-ray-head-ssh'
    return get_port(svc_name, namespace)


def get_port(svc_name: str, namespace: str) -> int:
    """Gets the nodeport of the specified service.

    Args:
        svc_name (str): Name of the kubernetes service. Note that this may be
            different from the cluster name.
        namespace (str): Kubernetes namespace to look for the service in.
    """
    head_service = kubernetes.core_api().read_namespaced_service(
        svc_name, namespace)
    return head_service.spec.ports[0].node_port


def get_external_ip():
    # Return the IP address of the first node with an external IP
    nodes = kubernetes.core_api().list_node().items
    for node in nodes:
        if node.status.addresses:
            for address in node.status.addresses:
                if address.type == 'ExternalIP':
                    return address.address
    # If no external IP is found, use the API server IP
    api_host = kubernetes.core_api().api_client.configuration.host
    parsed_url = urlparse(api_host)
    return parsed_url.hostname


def check_credentials(timeout: int = kubernetes.API_TIMEOUT) -> \
        Tuple[bool, Optional[str]]:
    """Check if the credentials in kubeconfig file are valid

    Args:
        timeout (int): Timeout in seconds for the test API call

    Returns:
        bool: True if credentials are valid, False otherwise
        str: Error message if credentials are invalid, None otherwise
    """
    try:
        ns = get_current_kube_config_context_namespace()
        kubernetes.core_api().list_namespaced_pod(ns, _request_timeout=timeout)
        return True, None
    except ImportError:
        # TODO(romilb): Update these error strs to also include link to docs
        #  when docs are ready.
        return False, '`kubernetes` package is not installed. ' \
                      'Install it with: pip install kubernetes'
    except kubernetes.api_exception() as e:
        # Check if the error is due to invalid credentials
        if e.status == 401:
            return False, 'Invalid credentials - do you have permission ' \
                          'to access the cluster?'
        else:
            return False, f'Failed to communicate with the cluster: {str(e)}'
    except kubernetes.config_exception() as e:
        return False, f'Invalid configuration file: {str(e)}'
    except kubernetes.max_retry_error():
        return False, 'Failed to communicate with the cluster - timeout. ' \
                      'Check if your cluster is running and your network ' \
                      'is stable.'
    except ValueError as e:
        return False, common_utils.format_exception(e)
    except Exception as e:  # pylint: disable=broad-except
        return False, f'An error occurred: {str(e)}'


def get_current_kube_config_context_name() -> Optional[str]:
    """Get the current kubernetes context from the kubeconfig file

    Returns:
        str | None: The current kubernetes context if it exists, None otherwise
    """
    k8s = kubernetes.get_kubernetes()
    try:
        _, current_context = k8s.config.list_kube_config_contexts()
        return current_context['name']
    except k8s.config.config_exception.ConfigException:
        return None


def get_current_kube_config_context_namespace() -> str:
    """Get the current kubernetes context namespace from the kubeconfig file

    Returns:
        str | None: The current kubernetes context namespace if it exists, else
            the default namespace.
    """
    k8s = kubernetes.get_kubernetes()
    try:
        _, current_context = k8s.config.list_kube_config_contexts()
        if 'namespace' in current_context['context']:
            return current_context['context']['namespace']
        else:
            return DEFAULT_NAMESPACE
    except k8s.config.config_exception.ConfigException:
        return DEFAULT_NAMESPACE


def _get_proxy_command(network_mode: KubernetesNetworkingMode,
                       private_key_path: str,
                       ssh_jump_port: int,
                       ssh_jump_ip: str,
                       port_forward_proxy_cmd_path: str = '') -> str:
    if network_mode == KubernetesNetworkingMode.NODEPORT:
        ssh_jump_proxy_command = (f'ssh -tt -i {private_key_path} '
                                  '-o StrictHostKeyChecking=no '
                                  '-o UserKnownHostsFile=/dev/null '
                                  f'-o IdentitiesOnly=yes -p {ssh_jump_port} '
                                  f'-W %h:%p sky@{ssh_jump_ip}')
    else:  # network_mode == KubernetesNetworkingMode.PORT_FORWARD:
        ssh_jump_proxy_command = (
            f'ssh -tt -i {private_key_path} '
            f'-o ProxyCommand=\'{port_forward_proxy_cmd_path}\' '
            '-o StrictHostKeyChecking=no '
            '-o UserKnownHostsFile=/dev/null '
            f'-o IdentitiesOnly=yes -p {ssh_jump_port} '
            f'-W %h:%p sky@{ssh_jump_ip}')
    return ssh_jump_proxy_command


def get_ssh_proxy_command(private_key_path: str, ssh_jump_name: str,
                          network_mode: KubernetesNetworkingMode,
                          namespace: str, kube_config_path: str,
                          port_fwd_proxy_cmd_path: str,
                          port_fwd_proxy_cmd_template: str) -> str:
    """Generates the SSH proxy command to connect through the SSH jump pod.

    By default, establishing an SSH connection creates a communication
    channel to a remote node by setting up a TCP connection. When a
    ProxyCommand is specified, this default behavior is overridden. The command
    specified in ProxyCommand is executed, and its standard input and output
    become the communication channel for the SSH session.

    Pods within a Kubernetes cluster have internal IP addresses that are
    typically not accessible from outside the cluster. Since the default TCP
    connection of SSH won't allow access to these pods, we employ a
    ProxyCommand to establish the required communication channel. We offer this
    in two different networking options: NodePort/port-forward.

    With the NodePort networking mode, a NodePort service is launched. This
    service opens an external port on the node which redirects to the desired
    port within the pod. When establishing an SSH session in this mode, the
    ProxyCommand makes use of this external port to create a communication
    channel directly to port 22, which is the default port ssh server listens
    on, of the jump pod.

    With Port-forward mode, instead of directly exposing an external port,
    'kubectl port-forward' sets up a tunnel between a local port
    (127.0.0.1:23100) and port 22 of the jump pod. Then we establish a TCP
    connection to the local end of this tunnel, 127.0.0.1:23100, using 'socat'.
    This is setup in the inner ProxyCommand of the nested ProxyCommand, and the
    rest is the same as NodePort approach, which the outer ProxyCommand
    establishes a communication channel between 127.0.0.1:23100 and port 22 on
    the jump pod. Consequently, any stdin provided on the local machine is
    forwarded through this tunnel to the application (SSH server) listening in
    the pod. Similarly, any output from the application in the pod is tunneled
    back and displayed in the terminal on the local machine.

    Args:
        private_key_path: str; Path to the private key to use for SSH.
            This key must be authorized to access the SSH jump pod.
        ssh_jump_name: str; Name of the SSH jump service to use
        network_mode: KubernetesNetworkingMode; networking mode for ssh
            session. It is either 'NODEPORT' or 'PORT_FORWARD'
        namespace: Kubernetes namespace to use
        kube_config_path: str; path to kubernetes config
        port_fwd_proxy_cmd_path: str; path to the script used as Proxycommand
            with 'kubectl port-forward'
        port_fwd_proxy_cmd_template: str; template used to create
            'kubectl port-forward' Proxycommand
    """
    # Fetch IP to connect to for the jump svc
    ssh_jump_ip = get_external_ip()
    if network_mode == KubernetesNetworkingMode.NODEPORT:
        ssh_jump_port = get_port(ssh_jump_name, namespace)
        ssh_jump_proxy_command = _get_proxy_command(network_mode,
                                                    private_key_path,
                                                    ssh_jump_port, ssh_jump_ip)
    # Setting kubectl port-forward/socat to establish ssh session using
    # ClusterIP service to disallow any ports opened
    else:
        ssh_jump_port = LOCAL_PORT_FOR_PORT_FORWARD
        kube_config_path = os.path.expanduser(kube_config_path)
        vars_to_fill = {
            'ssh_jump_name': ssh_jump_name,
            'local_port': ssh_jump_port,
        }
        port_forward_proxy_cmd_path = os.path.expanduser(
            port_fwd_proxy_cmd_path)
        backend_utils.fill_template(port_fwd_proxy_cmd_template,
                                    vars_to_fill,
                                    output_path=port_forward_proxy_cmd_path)
        os.chmod(port_forward_proxy_cmd_path,
                 os.stat(port_forward_proxy_cmd_path).st_mode | 0o111)
        ssh_jump_proxy_command = _get_proxy_command(
            network_mode, private_key_path, ssh_jump_port, ssh_jump_ip,
            port_forward_proxy_cmd_path)

    return ssh_jump_proxy_command


def setup_sshjump_svc(ssh_jump_name: str, namespace: str, service_type: str):
    """Sets up Kubernetes service resource to access for SSH jump pod.

    This method acts as a necessary complement to be run along with
    setup_sshjump_pod(...) method. This service ensures the pod is accessible.

    Args:
        sshjump_name: Name to use for the SSH jump service
        namespace: Namespace to create the SSH jump service in
        service_type: Networking configuration on either to use NodePort
            or ClusterIP service to ssh in
    """
    # Fill in template - ssh_key_secret and sshjump_image are not required for
    # the service spec, so we pass in empty strs.
    content = fill_sshjump_template('', '', ssh_jump_name, service_type)
    # Create service
    try:
        kubernetes.core_api().create_namespaced_service(namespace,
                                                        content['service_spec'])
    except kubernetes.api_exception() as e:
        # SSH Jump Pod service already exists.
        if e.status == 409:
            ssh_jump_service = kubernetes.core_api().read_namespaced_service(
                name=ssh_jump_name, namespace=namespace)
            curr_svc_type = ssh_jump_service.spec.type
            if service_type == curr_svc_type:
                # If the currently existing SSH Jump service's type is identical
                # to user's configuration for networking mode
                logger.warning(
                    f'SSH Jump Service {ssh_jump_name} already exists in the '
                    'cluster, using it.')
            else:
                # If a different type of service type for SSH Jump pod compared
                # to user's configuration for networking mode exists, we remove
                # existing servie to create a new one following user's config
                kubernetes.core_api().delete_namespaced_service(
                    name=ssh_jump_name, namespace=namespace)
                kubernetes.core_api().create_namespaced_service(
                    namespace, content['service_spec'])
                curr_network_mode = 'Port-Forward' \
                    if curr_svc_type == 'ClusterIP' else 'NodePort'
                new_network_mode = 'NodePort' \
                    if curr_svc_type == 'ClusterIP' else 'Port-Forward'
                new_svc_type = 'NodePort' \
                    if curr_svc_type == 'ClusterIP' else 'ClusterIP'
                logger.info(
                    f'Switching the networking mode from '
                    f'\'{curr_network_mode}\' to \'{new_network_mode}\' '
                    f'following networking configuration. Deleting existing '
                    f'\'{curr_svc_type}\' service and recreating as '
                    f'\'{new_svc_type}\' service.')
        else:
            raise
    else:
        logger.info(f'Created SSH Jump Service {ssh_jump_name}.')


def setup_sshjump_pod(sshjump_name: str, sshjump_image: str,
                      ssh_key_secret: str, namespace: str):
    """Sets up Kubernetes RBAC and pod for SSH jump host.

    Our Kubernetes implementation uses a SSH jump pod to reach SkyPilot clusters
    running inside a cluster. This function sets up the resources needed for
    the SSH jump pod. This includes a service account which grants the jump pod
    permission to watch for other SkyPilot pods and terminate itself if there
    are no SkyPilot pods running.

    setup_sshjump_service must also be run to ensure that the SSH jump pod is
    reachable.

    Args:
        sshjump_image: Container image to use for the SSH jump pod
        sshjump_name: Name to use for the SSH jump pod
        ssh_key_secret: Secret name for the SSH key stored in the cluster
        namespace: Namespace to create the SSH jump pod in
    """
    # Fill in template - service is created separately so service_type is not
    # required, so we pass in empty str.
    content = fill_sshjump_template(ssh_key_secret, sshjump_image, sshjump_name,
                                    '')
    # ServiceAccount
    try:
        kubernetes.core_api().create_namespaced_service_account(
            namespace, content['service_account'])
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info(
                'SSH Jump ServiceAcount already exists in the cluster, using '
                'it.')
        else:
            raise
    else:
        logger.info('Created SSH Jump ServiceAcount.')
    # Role
    try:
        kubernetes.auth_api().create_namespaced_role(namespace, content['role'])
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info(
                'SSH Jump Role already exists in the cluster, using it.')
        else:
            raise
    else:
        logger.info('Created SSH Jump Role.')
    # RoleBinding
    try:
        kubernetes.auth_api().create_namespaced_role_binding(
            namespace, content['role_binding'])
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info(
                'SSH Jump RoleBinding already exists in the cluster, using '
                'it.')
        else:
            raise
    else:
        logger.info('Created SSH Jump RoleBinding.')
    # Pod
    try:
        kubernetes.core_api().create_namespaced_pod(namespace,
                                                    content['pod_spec'])
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.info(
                f'SSH Jump Host {sshjump_name} already exists in the cluster, '
                'using it.')
        else:
            raise
    else:
        logger.info(f'Created SSH Jump Host {sshjump_name}.')


def fill_sshjump_template(ssh_key_secret: str, sshjump_image: str,
                          sshjump_name: str, service_type: str) -> Dict:
    template_path = os.path.join(sky.__root_dir__, 'templates',
                                 'kubernetes-sshjump.yml.j2')
    if not os.path.exists(template_path):
        raise FileNotFoundError(
            'Template "kubernetes-sshjump.j2" does not exist.')
    with open(template_path) as fin:
        template = fin.read()
    j2_template = jinja2.Template(template)
    cont = j2_template.render(name=sshjump_name,
                              image=sshjump_image,
                              secret=ssh_key_secret,
                              service_type=service_type)
    content = yaml.safe_load(cont)
    return content
