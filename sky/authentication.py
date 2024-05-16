"""Module to enable a single SkyPilot key for all VMs in each cloud.

The `setup_<cloud>_authentication` functions will be called on every cluster
provisioning request.

Specifically, after the ray yaml template file `<cloud>-ray.yml.j2` is filled in
with resource specific information, these functions are called with the filled
in ray yaml config as input,
1. Replace the placeholders in the ray yaml file `skypilot:ssh_user` and
   `skypilot:ssh_public_key_content` with the actual username and public key
   content, i.e., `configure_ssh_info`.
2. Setup the `authorized_keys` on the remote VM with the public key content,
   by cloud-init or directly using cloud provider's API.

The local machine's public key should not be uploaded to the
`~/.ssh/sky-key.pub` on the remote VM, because it will cause private/public
key pair mismatch when the user tries to launch new VM from that remote VM
using SkyPilot, e.g., the node is used as a jobs controller. (Lambda cloud
is an exception, due to the limitation of the cloud provider. See the
comments in setup_lambda_authentication)
"""
import base64
import copy
import functools
import os
import re
import socket
import subprocess
import sys
from typing import Any, Dict, Tuple
import uuid

import colorama
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import filelock
import yaml

from sky import clouds
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import gcp
from sky.adaptors import ibm
from sky.adaptors import kubernetes
from sky.adaptors import runpod
from sky.clouds.utils import lambda_utils
from sky.provision.fluidstack import fluidstack_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import common_utils
from sky.utils import kubernetes_enums
from sky.utils import subprocess_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# TODO: Should tolerate if gcloud is not installed. Also,
# https://pypi.org/project/google-api-python-client/ recommends
# using Cloud Client Libraries for Python, where possible, for new code
# development.

MAX_TRIALS = 64
# TODO(zhwu): Support user specified key pair.
PRIVATE_SSH_KEY_PATH = '~/.ssh/sky-key'
PUBLIC_SSH_KEY_PATH = '~/.ssh/sky-key.pub'
_SSH_KEY_GENERATION_LOCK = '~/.sky/generated/ssh/.__internal-sky-key.lock'


def _generate_rsa_key_pair() -> Tuple[str, str]:
    key = rsa.generate_private_key(backend=default_backend(),
                                   public_exponent=65537,
                                   key_size=2048)

    private_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()).decode(
            'utf-8').strip()

    public_key = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH).decode('utf-8').strip()

    return public_key, private_key


def _save_key_pair(private_key_path: str, public_key_path: str,
                   private_key: str, public_key: str) -> None:
    key_dir = os.path.dirname(private_key_path)
    os.makedirs(key_dir, exist_ok=True, mode=0o700)

    with open(
            private_key_path,
            'w',
            encoding='utf-8',
            opener=functools.partial(os.open, mode=0o600),
    ) as f:
        f.write(private_key)

    with open(public_key_path,
              'w',
              encoding='utf-8',
              opener=functools.partial(os.open, mode=0o644)) as f:
        f.write(public_key)


def get_or_generate_keys() -> Tuple[str, str]:
    """Returns the aboslute private and public key paths."""
    private_key_path = os.path.expanduser(PRIVATE_SSH_KEY_PATH)
    public_key_path = os.path.expanduser(PUBLIC_SSH_KEY_PATH)

    key_file_lock = os.path.expanduser(_SSH_KEY_GENERATION_LOCK)
    lock_dir = os.path.dirname(key_file_lock)
    # We should have the folder ~/.sky/generated/ssh to have 0o700 permission,
    # as the ssh configs will be written to this folder as well in
    # backend_utils.SSHConfigHelper
    os.makedirs(lock_dir, exist_ok=True, mode=0o700)
    with filelock.FileLock(key_file_lock, timeout=10):
        if not os.path.exists(private_key_path):
            public_key, private_key = _generate_rsa_key_pair()
            _save_key_pair(private_key_path, public_key_path, private_key,
                           public_key)
    assert os.path.exists(public_key_path), (
        'Private key found, but associated public key '
        f'{public_key_path} does not exist.')
    return private_key_path, public_key_path


def configure_ssh_info(config: Dict[str, Any]) -> Dict[str, Any]:
    _, public_key_path = get_or_generate_keys()
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read().strip()
    config_str = common_utils.dump_yaml_str(config)
    config_str = config_str.replace('skypilot:ssh_user',
                                    config['auth']['ssh_user'])
    config_str = config_str.replace('skypilot:ssh_public_key_content',
                                    public_key)
    config = yaml.safe_load(config_str)
    return config


# Snippets of code inspired from
# https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/gcp/config.py
# Takes in config, a yaml dict and outputs a postprocessed dict
# TODO(weilin): refactor the implementation to incorporate Ray autoscaler to
# avoid duplicated codes.
# Retry for the GCP as sometimes there will be connection reset by peer error.
@common_utils.retry
def setup_gcp_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    _, public_key_path = get_or_generate_keys()
    config = copy.deepcopy(config)

    project_id = config['provider']['project_id']
    compute = gcp.build('compute',
                        'v1',
                        credentials=None,
                        cache_discovery=False)

    try:
        project = compute.projects().get(project=project_id).execute()
    except gcp.http_error_exception() as e:
        # Can happen for a new project where Compute Engine API is disabled.
        #
        # Example message:
        # 'Compute Engine API has not been used in project 123456 before
        # or it is disabled. Enable it by visiting
        # https://console.developers.google.com/apis/api/compute.googleapis.com/overview?project=123456
        # then retry. If you enabled this API recently, wait a few minutes for
        # the action to propagate to our systems and retry.'
        if ' API has not been used in project' in e.reason:
            match = re.fullmatch(r'(.+)(https://.*project=\d+) (.+)', e.reason)
            if match is None:
                raise  # This should not happen.
            yellow = colorama.Fore.YELLOW
            reset = colorama.Style.RESET_ALL
            bright = colorama.Style.BRIGHT
            dim = colorama.Style.DIM
            logger.error(
                f'{yellow}Certain GCP APIs are disabled for the GCP project '
                f'{project_id}.{reset}')
            logger.error('Details:')
            logger.error(f'{dim}{match.group(1)}{reset}\n'
                         f'{dim}    {match.group(2)}{reset}\n'
                         f'{dim}{match.group(3)}{reset}')
            logger.error(
                f'{yellow}To fix, enable these APIs by running:{reset} '
                f'{bright}sky check{reset}')
            sys.exit(1)
        else:
            raise
    except socket.timeout:
        logger.error('Socket timed out when trying to get the GCP project. '
                     'Please check your network connection.')
        raise

    project_oslogin: str = next(  # type: ignore
        (item for item in project['commonInstanceMetadata'].get('items', [])
         if item['key'] == 'enable-oslogin'), {}).get('value', 'False')

    if project_oslogin.lower() == 'true':
        logger.info(
            f'OS Login is enabled for GCP project {project_id}. Running '
            'additional authentication steps.')

        # Try to get the os-login user from `gcloud`, as this is the most
        # accurate way to figure out how this gcp user is meant to log in.
        proc = subprocess.run(
            'gcloud compute os-login describe-profile --format yaml',
            shell=True,
            stdout=subprocess.PIPE,
            check=False)
        os_login_username = None
        if proc.returncode == 0:
            try:
                profile = yaml.safe_load(proc.stdout)
                username = profile['posixAccounts'][0]['username']
                if username:
                    os_login_username = username
            except Exception as e:  # pylint: disable=broad-except
                logger.debug('Failed to parse gcloud os-login profile.\n'
                             f'{common_utils.format_exception(e)}')
                pass

        if os_login_username is None:
            # As a fallback, read the account information from the credential
            # file. This works most of the time, but fails if the user's
            # os-login username is not a straightforward translation of their
            # email address, for example because their email address changed
            # within their google workspace after the os-login credentials
            # were established.
            config_path = os.path.expanduser(clouds.gcp.GCP_CONFIG_PATH)
            with open(config_path, 'r', encoding='utf-8') as infile:
                for line in infile:
                    if line.startswith('account'):
                        account = line.split('=')[1].strip()
                        break
                else:
                    with ux_utils.print_exception_no_traceback():
                        raise RuntimeError(
                            'GCP authentication failed, as the oslogin is '
                            f'enabled but the file {config_path} does not '
                            'contain the account information.')
            os_login_username = account.replace('@', '_').replace('.', '_')
        config['auth']['ssh_user'] = os_login_username

        # Add ssh key to GCP with oslogin
        subprocess.run(
            'gcloud compute os-login ssh-keys add '
            f'--key-file={public_key_path}',
            check=True,
            shell=True,
            stdout=subprocess.DEVNULL)
        # Enable ssh port for all the instances
        enable_ssh_cmd = ('gcloud compute firewall-rules create '
                          'allow-ssh-ingress-from-iap '
                          '--direction=INGRESS '
                          '--action=allow '
                          '--rules=tcp:22 '
                          '--source-ranges=0.0.0.0/0')
        proc = subprocess.run(enable_ssh_cmd,
                              check=False,
                              shell=True,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE)
        if proc.returncode != 0 and 'already exists' not in proc.stderr.decode(
                'utf-8'):
            subprocess_utils.handle_returncode(proc.returncode, enable_ssh_cmd,
                                               'Failed to enable ssh port.',
                                               proc.stderr.decode('utf-8'))
    return configure_ssh_info(config)


# In Azure, cloud-init script must be encoded in base64. See
# https://learn.microsoft.com/en-us/azure/virtual-machines/custom-data
# for more information. Here we decode it and replace the ssh user
# and public key content, then encode it back.
def setup_azure_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    _, public_key_path = get_or_generate_keys()
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read().strip()
    for node_type in config['available_node_types']:
        node_config = config['available_node_types'][node_type]['node_config']
        cloud_init = (
            node_config['azure_arm_parameters']['cloudInitSetupCommands'])
        cloud_init = base64.b64decode(cloud_init).decode('utf-8')
        cloud_init = cloud_init.replace('skypilot:ssh_user',
                                        config['auth']['ssh_user'])
        cloud_init = cloud_init.replace('skypilot:ssh_public_key_content',
                                        public_key)
        cloud_init = base64.b64encode(
            cloud_init.encode('utf-8')).decode('utf-8')
        node_config['azure_arm_parameters']['cloudInitSetupCommands'] = (
            cloud_init)
    config_str = common_utils.dump_yaml_str(config)
    config_str = config_str.replace('skypilot:ssh_user',
                                    config['auth']['ssh_user'])
    config_str = config_str.replace('skypilot:ssh_public_key_content',
                                    public_key)
    config = yaml.safe_load(config_str)
    return config


def setup_lambda_authentication(config: Dict[str, Any]) -> Dict[str, Any]:

    get_or_generate_keys()

    # Ensure ssh key is registered with Lambda Cloud
    lambda_client = lambda_utils.LambdaCloudClient()
    public_key_path = os.path.expanduser(PUBLIC_SSH_KEY_PATH)
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read().strip()
    prefix = f'sky-key-{common_utils.get_user_hash()}'
    name, exists = lambda_client.get_unique_ssh_key_name(prefix, public_key)
    if not exists:
        lambda_client.register_ssh_key(name, public_key)

    # Need to use ~ relative path because Ray uses the same
    # path for finding the public key path on both local and head node.
    config['auth']['ssh_public_key'] = PUBLIC_SSH_KEY_PATH

    # TODO(zhwu): we need to avoid uploading the public ssh key to the
    # nodes, as that will cause problem when the node is used as spot
    # controller, i.e., the public and private key on the node may
    # not match.
    file_mounts = config['file_mounts']
    file_mounts[PUBLIC_SSH_KEY_PATH] = PUBLIC_SSH_KEY_PATH
    config['file_mounts'] = file_mounts

    return config


def setup_ibm_authentication(config):
    """ registers keys if they do not exist in sky folder
    and updates config file.
    keys default location: '~/.ssh/sky-key' and '~/.ssh/sky-key.pub'
    """

    def _get_unique_key_name():
        suffix_len = 10
        return f'skypilot-key-{str(uuid.uuid4())[:suffix_len]}'

    client = ibm.client(region=config['provider']['region'])
    resource_group_id = config['provider']['resource_group_id']

    _, public_key_path = get_or_generate_keys()
    with open(os.path.abspath(os.path.expanduser(public_key_path)),
              'r',
              encoding='utf-8') as file:
        ssh_key_data = file.read().strip()
    # pylint: disable=E1136
    try:
        res = client.create_key(public_key=ssh_key_data,
                                name=_get_unique_key_name(),
                                resource_group={
                                    'id': resource_group_id
                                },
                                type='rsa').get_result()
        vpc_key_id = res['id']
        logger.debug(f'Created new key: {res["name"]}')

    except ibm.ibm_cloud_sdk_core.ApiException as e:
        if 'Key with fingerprint already exists' in e.message:
            for key in client.list_keys().result['keys']:
                if (ssh_key_data in key['public_key'] or
                        key['public_key'] in ssh_key_data):
                    vpc_key_id = key['id']
                    logger.debug(f'Reusing key:{key["name"]}, '
                                 f'matching existing public key.')
                    break
        elif 'Key with name already exists' in e.message:
            raise Exception("""a key with chosen name
                already registered in the specified region""") from e
        else:
            raise Exception('Failed to register a key') from e

    config['auth']['ssh_private_key'] = PRIVATE_SSH_KEY_PATH

    for node_type in config['available_node_types']:
        config['available_node_types'][node_type]['node_config'][
            'key_id'] = vpc_key_id

    # Add public key path to file mounts
    file_mounts = config['file_mounts']
    file_mounts[PUBLIC_SSH_KEY_PATH] = PUBLIC_SSH_KEY_PATH
    config['file_mounts'] = file_mounts

    return config


def setup_kubernetes_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    # Default ssh session is established with kubectl port-forwarding with
    # ClusterIP service.
    nodeport_mode = kubernetes_enums.KubernetesNetworkingMode.NODEPORT
    port_forward_mode = kubernetes_enums.KubernetesNetworkingMode.PORTFORWARD
    network_mode_str = skypilot_config.get_nested(('kubernetes', 'networking'),
                                                  port_forward_mode.value)
    try:
        network_mode = kubernetes_enums.KubernetesNetworkingMode.from_str(
            network_mode_str)
    except ValueError as e:
        # Add message saying "Please check: ~/.sky/config.yaml" to the error
        # message.
        with ux_utils.print_exception_no_traceback():
            raise ValueError(str(e) + ' Please check: ~/.sky/config.yaml.') \
                from None
    get_or_generate_keys()

    # Add the user's public key to the SkyPilot cluster.
    public_key_path = os.path.expanduser(PUBLIC_SSH_KEY_PATH)
    secret_name = clouds.Kubernetes.SKY_SSH_KEY_SECRET_NAME
    secret_field_name = clouds.Kubernetes().ssh_key_secret_field_name
    namespace = kubernetes_utils.get_current_kube_config_context_namespace()
    k8s = kubernetes.kubernetes
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read()
        if not public_key.endswith('\n'):
            public_key += '\n'

        # Generate metadata
        secret_metadata = {
            'name': secret_name,
            'labels': {
                'parent': 'skypilot'
            }
        }
        custom_metadata = skypilot_config.get_nested(
            ('kubernetes', 'custom_metadata'), {})
        kubernetes_utils.merge_dicts(custom_metadata, secret_metadata)

        secret = k8s.client.V1Secret(
            metadata=k8s.client.V1ObjectMeta(**secret_metadata),
            string_data={secret_field_name: public_key})
    if kubernetes_utils.check_secret_exists(secret_name, namespace):
        logger.debug(f'Key {secret_name} exists in the cluster, patching it...')
        kubernetes.core_api().patch_namespaced_secret(secret_name, namespace,
                                                      secret)
    else:
        logger.debug(
            f'Key {secret_name} does not exist in the cluster, creating it...')
        kubernetes.core_api().create_namespaced_secret(namespace, secret)

    ssh_jump_name = clouds.Kubernetes.SKY_SSH_JUMP_NAME
    if network_mode == nodeport_mode:
        service_type = kubernetes_enums.KubernetesServiceType.NODEPORT
    elif network_mode == port_forward_mode:
        kubernetes_utils.check_port_forward_mode_dependencies()
        # Using `kubectl port-forward` creates a direct tunnel to jump pod and
        # does not require opening any ports on Kubernetes nodes. As a result,
        # the service can be a simple ClusterIP service which we access with
        # `kubectl port-forward`.
        service_type = kubernetes_enums.KubernetesServiceType.CLUSTERIP
    else:
        # This should never happen because we check for this in from_str above.
        raise ValueError(f'Unsupported networking mode: {network_mode_str}')
    # Setup service for SSH jump pod. We create the SSH jump service here
    # because we need to know the service IP address and port to set the
    # ssh_proxy_command in the autoscaler config.
    kubernetes_utils.setup_ssh_jump_svc(ssh_jump_name, namespace, service_type)

    ssh_proxy_cmd = kubernetes_utils.get_ssh_proxy_command(
        PRIVATE_SSH_KEY_PATH, ssh_jump_name, network_mode, namespace,
        clouds.Kubernetes.PORT_FORWARD_PROXY_CMD_PATH,
        clouds.Kubernetes.PORT_FORWARD_PROXY_CMD_TEMPLATE)

    config['auth']['ssh_proxy_command'] = ssh_proxy_cmd

    return config


# ---------------------------------- RunPod ---------------------------------- #
def setup_runpod_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    """Sets up SSH authentication for RunPod.
    - Generates a new SSH key pair if one does not exist.
    - Adds the public SSH key to the user's RunPod account.
    """
    _, public_key_path = get_or_generate_keys()
    with open(public_key_path, 'r', encoding='UTF-8') as pub_key_file:
        public_key = pub_key_file.read().strip()
        runpod.runpod.cli.groups.ssh.functions.add_ssh_key(public_key)

    return configure_ssh_info(config)


def setup_fluidstack_authentication(config: Dict[str, Any]) -> Dict[str, Any]:

    get_or_generate_keys()

    client = fluidstack_utils.FluidstackClient()
    public_key_path = os.path.expanduser(PUBLIC_SSH_KEY_PATH)
    public_key = None
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read()
    client.get_or_add_ssh_key(public_key)
    config['auth']['ssh_public_key'] = PUBLIC_SSH_KEY_PATH
    return configure_ssh_info(config)
