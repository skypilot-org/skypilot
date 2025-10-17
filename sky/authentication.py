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

The local machine's public key should not be uploaded to the remote VM, because
it will cause private/public key pair mismatch when the user tries to launch new
VM from that remote VM using SkyPilot, e.g., the node is used as a jobs
controller. (Lambda cloud is an exception, due to the limitation of the cloud
provider. See the comments in setup_lambda_authentication)
"""
import copy
import os
import re
import socket
import subprocess
import sys
from typing import Any, Dict
import uuid

import colorama

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.adaptors import gcp
from sky.adaptors import ibm
from sky.adaptors import runpod
from sky.adaptors import seeweb as seeweb_adaptor
from sky.adaptors import shadeform as shadeform_adaptor
from sky.adaptors import vast
from sky.provision.fluidstack import fluidstack_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.provision.lambda_cloud import lambda_utils
from sky.provision.primeintellect import utils as primeintellect_utils
from sky.utils import auth_utils
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

# TODO: Should tolerate if gcloud is not installed. Also,
# https://pypi.org/project/google-api-python-client/ recommends
# using Cloud Client Libraries for Python, where possible, for new code
# development.


def configure_ssh_info(config: Dict[str, Any]) -> Dict[str, Any]:
    _, public_key_path = auth_utils.get_or_generate_keys()
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read().strip()
    config_str = yaml_utils.dump_yaml_str(config)
    config_str = config_str.replace('skypilot:ssh_user',
                                    config['auth']['ssh_user'])
    config_str = config_str.replace('skypilot:ssh_public_key_content',
                                    public_key)
    config = yaml_utils.safe_load(config_str)
    return config


def parse_gcp_project_oslogin(project):
    """Helper function to parse GCP project metadata."""
    common_metadata = project.get('commonInstanceMetadata', {})
    if not isinstance(common_metadata, dict):
        common_metadata = {}

    metadata_items = common_metadata.get('items', [])
    if not isinstance(metadata_items, list):
        metadata_items = []

    project_oslogin = next(
        (item for item in metadata_items
         if isinstance(item, dict) and item.get('key') == 'enable-oslogin'),
        {}).get('value', 'False')

    return project_oslogin


# Snippets of code inspired from
# https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/gcp/config.py
# Takes in config, a yaml dict and outputs a postprocessed dict
# TODO(weilin): refactor the implementation to incorporate Ray autoscaler to
# avoid duplicated codes.
# Retry for the GCP as sometimes there will be connection reset by peer error.
@common_utils.retry
def setup_gcp_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    _, public_key_path = auth_utils.get_or_generate_keys()
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
    except gcp.auth_error_exception() as e:
        raise exceptions.InvalidCloudCredentials(
            f'{common_utils.format_exception(e)}')
    except socket.timeout:
        logger.error('Socket timed out when trying to get the GCP project. '
                     'Please check your network connection.')
        raise

    project_oslogin = parse_gcp_project_oslogin(project)
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
                profile = yaml_utils.safe_load(proc.stdout)
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


def setup_lambda_authentication(config: Dict[str, Any]) -> Dict[str, Any]:

    auth_utils.get_or_generate_keys()

    # Ensure ssh key is registered with Lambda Cloud
    lambda_client = lambda_utils.LambdaCloudClient()
    _, public_key_path = auth_utils.get_or_generate_keys()
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read().strip()
    prefix = f'sky-key-{common_utils.get_user_hash()}'
    name, exists = lambda_client.get_unique_ssh_key_name(prefix, public_key)
    if not exists:
        lambda_client.register_ssh_key(name, public_key)

    config['auth']['remote_key_name'] = name
    return config


def setup_ibm_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    """ registers keys if they do not exist in sky folder
    and updates config file.
    keys default location: '~/.ssh/sky-key' and '~/.ssh/sky-key.pub'
    """
    private_key_path, _ = auth_utils.get_or_generate_keys()

    def _get_unique_key_name():
        suffix_len = 10
        return f'skypilot-key-{str(uuid.uuid4())[:suffix_len]}'

    client = ibm.client(region=config['provider']['region'])
    resource_group_id = config['provider']['resource_group_id']

    _, public_key_path = auth_utils.get_or_generate_keys()
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

    config['auth']['ssh_private_key'] = private_key_path

    for node_type in config['available_node_types']:
        config['available_node_types'][node_type]['node_config'][
            'key_id'] = vpc_key_id
    return config


def setup_kubernetes_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    context = kubernetes_utils.get_context_from_config(config['provider'])
    namespace = kubernetes_utils.get_namespace_from_config(config['provider'])
    private_key_path, _ = auth_utils.get_or_generate_keys()
    # Using `kubectl port-forward` creates a direct tunnel to the pod and
    # does not require a ssh jump pod.
    kubernetes_utils.check_port_forward_mode_dependencies()
    # TODO(romilb): This can be further optimized. Instead of using the
    #   head node as a jump pod for worker nodes, we can also directly
    #   set the ssh_target to the worker node. However, that requires
    #   changes in the downstream code to return a mapping of node IPs to
    #   pod names (to be used as ssh_target) and updating the upstream
    #   SSHConfigHelper to use a different ProxyCommand for each pod.
    #   This optimization can reduce SSH time from ~0.35s to ~0.25s, tested
    #   on GKE.
    pod_name = config['cluster_name'] + '-head'
    ssh_proxy_cmd = kubernetes_utils.get_ssh_proxy_command(
        pod_name,
        private_key_path=private_key_path,
        context=context,
        namespace=namespace)
    config['auth']['ssh_proxy_command'] = ssh_proxy_cmd
    config['auth']['ssh_private_key'] = private_key_path

    # Add the user's public key to the SkyPilot cluster.
    return configure_ssh_info(config)


# ---------------------------------- RunPod ---------------------------------- #
def setup_runpod_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    """Sets up SSH authentication for RunPod.
    - Generates a new SSH key pair if one does not exist.
    - Adds the public SSH key to the user's RunPod account.
    """
    _, public_key_path = auth_utils.get_or_generate_keys()
    with open(public_key_path, 'r', encoding='UTF-8') as pub_key_file:
        public_key = pub_key_file.read().strip()
        runpod.runpod.cli.groups.ssh.functions.add_ssh_key(public_key)

    return configure_ssh_info(config)


def setup_vast_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    """Sets up SSH authentication for Vast.
    - Generates a new SSH key pair if one does not exist.
    - Adds the public SSH key to the user's Vast account.
    """
    _, public_key_path = auth_utils.get_or_generate_keys()
    with open(public_key_path, 'r', encoding='UTF-8') as pub_key_file:
        public_key = pub_key_file.read().strip()
        current_key_list = vast.vast().show_ssh_keys()  # pylint: disable=assignment-from-no-return
        # Only add an ssh key if it hasn't already been added
        if not any(x['public_key'] == public_key for x in current_key_list):
            vast.vast().create_ssh_key(ssh_key=public_key)

    config['auth']['ssh_public_key'] = public_key_path
    return configure_ssh_info(config)


def setup_fluidstack_authentication(config: Dict[str, Any]) -> Dict[str, Any]:

    _, public_key_path = auth_utils.get_or_generate_keys()

    client = fluidstack_utils.FluidstackClient()
    public_key = None
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read()
    client.get_or_add_ssh_key(public_key)
    config['auth']['ssh_public_key'] = public_key_path
    return configure_ssh_info(config)


def setup_hyperbolic_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    """Sets up SSH authentication for Hyperbolic."""
    _, public_key_path = auth_utils.get_or_generate_keys()
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read().strip()

    # TODO: adjust below to use public_keys instead of
    # public_key once backwards-compatibility is no longer required
    config['publicKey'] = public_key

    # Set up auth section for Ray template
    config.setdefault('auth', {})
    config['auth']['ssh_user'] = 'ubuntu'
    config['auth']['ssh_public_key'] = public_key_path

    return configure_ssh_info(config)


def setup_shadeform_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    """Sets up SSH authentication for Shadeform.
    - Generates a new SSH key pair if one does not exist.
    - Adds the public SSH key to the user's Shadeform account.

    Note: This assumes there is a Shadeform Python SDK available.
    If no official SDK exists, this function would need to use direct API calls.
    """

    _, public_key_path = auth_utils.get_or_generate_keys()
    ssh_key_id = None

    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read().strip()

    try:
        # Add SSH key to Shadeform using our utility functions
        ssh_key_id = shadeform_adaptor.add_ssh_key_to_shadeform(public_key)

    except ImportError as e:
        # If required dependencies are missing
        logger.warning(
            f'Failed to add Shadeform SSH key due to missing dependencies: '
            f'{e}. Manually configure SSH keys in your Shadeform account.')

    except Exception as e:
        logger.warning(f'Failed to set up Shadeform authentication: {e}')
        raise exceptions.CloudUserIdentityError(
            'Failed to set up SSH authentication for Shadeform. '
            f'Please ensure your Shadeform credentials are configured: {e}'
        ) from e

    if ssh_key_id is None:
        raise Exception('Failed to add SSH key to Shadeform')

    # Configure SSH info in the config
    config['auth']['ssh_public_key'] = public_key_path
    config['auth']['ssh_key_id'] = ssh_key_id

    return configure_ssh_info(config)


def setup_primeintellect_authentication(
        config: Dict[str, Any]) -> Dict[str, Any]:
    """Sets up SSH authentication for Prime Intellect.
    - Generates a new SSH key pair if one does not exist.
    - Adds the public SSH key to the user's Prime Intellect account.
    """
    # Ensure local SSH keypair exists and fetch public key content
    _, public_key_path = auth_utils.get_or_generate_keys()
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read().strip()

    # Register the public key with Prime Intellect (no-op if already exists)
    client = primeintellect_utils.PrimeIntellectAPIClient()
    client.get_or_add_ssh_key(public_key)

    # Set up auth section for Ray template
    config.setdefault('auth', {})
    # Default username for Prime Intellect images
    config['auth']['ssh_user'] = 'ubuntu'
    config['auth']['ssh_public_key'] = public_key_path

    return configure_ssh_info(config)


def setup_seeweb_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    """Registers the public key with Seeweb and notes the remote name."""
    # 1. local key pair
    auth_utils.get_or_generate_keys()

    # 2. public key
    _, public_key_path = auth_utils.get_or_generate_keys()
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read().strip()

    # 3. Seeweb API client
    client = seeweb_adaptor.client()

    # 4. Check if key is already registered
    prefix = f'sky-key-{common_utils.get_user_hash()}'
    remote_name = None
    for k in client.fetch_ssh_keys():
        if k.key.strip() == public_key:
            remote_name = k.label  # already present
            break

    # 5. doesn't exist, choose a unique name and create it
    if remote_name is None:
        suffix = 1
        remote_name = prefix
        existing_names = {k.label for k in client.fetch_ssh_keys()}
        while remote_name in existing_names:
            suffix += 1
            remote_name = f'{prefix}-{suffix}'
        client.create_ssh_key(label=remote_name, key=public_key)

    # 6. Put the remote name in cluster-config (like for Lambda)
    config['auth']['remote_key_name'] = remote_name

    return config
