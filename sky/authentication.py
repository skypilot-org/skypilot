"""Module to enable a single SkyPilot key for all VMs in each cloud.

The `setup_<cloud>_authentication` functions will be called on every cluster
provisioning request.

Specifically, after the ray yaml template file `<cloud>-ray.yml.j2` is filled in
with resource specific information, these functions are called with the filled
in ray yaml config as input,
1. Replace the placeholders in the ray yaml file `skypilot:ssh_user` and
   `skypilot:ssh_public_key_content` with the actual username and public key
   content, i.e., `_replace_ssh_info_in_config`.
2. Setup the `authorized_keys` on the remote VM with the public key content,
   by cloud-init or directly using cloud provider's API.

The local machine's public key should not be uploaded to the
`~/.ssh/sky-key.pub` on the remote VM, because it will cause private/public
key pair mismatch when the user tries to launch new VM from that remote VM
using SkyPilot, e.g., the node is used as a spot controller. (Lambda cloud
is an exception, due to the limitation of the cloud provider. See the
comments in setup_lambda_authentication)
"""
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
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import yaml

from sky import clouds
from sky import sky_logging
from sky.adaptors import gcp, ibm
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils
from sky.skylet.providers.lambda_cloud import lambda_utils

logger = sky_logging.init_logger(__name__)

# TODO: Should tolerate if gcloud is not installed. Also,
# https://pypi.org/project/google-api-python-client/ recommends
# using Cloud Client Libraries for Python, where possible, for new code
# development.

MAX_TRIALS = 64
# TODO(zhwu): Support user specified key pair.
PRIVATE_SSH_KEY_PATH = '~/.ssh/sky-key'
PUBLIC_SSH_KEY_PATH = '~/.ssh/sky-key.pub'


def _generate_rsa_key_pair() -> Tuple[str, str]:
    key = rsa.generate_private_key(backend=default_backend(),
                                   public_exponent=65537,
                                   key_size=2048)

    private_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()).decode('utf-8')

    public_key = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH).decode('utf-8')

    return public_key, private_key


def _save_key_pair(private_key_path: str, public_key_path: str,
                   private_key: str, public_key: str) -> None:
    private_key_dir = os.path.dirname(private_key_path)
    os.makedirs(private_key_dir, exist_ok=True)

    with open(
            private_key_path,
            'w',
            opener=functools.partial(os.open, mode=0o600),
    ) as f:
        f.write(private_key)

    with open(public_key_path, 'w') as f:
        f.write(public_key)


def get_or_generate_keys() -> Tuple[str, str]:
    """Returns the aboslute private and public key paths."""
    private_key_path = os.path.expanduser(PRIVATE_SSH_KEY_PATH)
    public_key_path = os.path.expanduser(PUBLIC_SSH_KEY_PATH)
    if not os.path.exists(private_key_path):
        public_key, private_key = _generate_rsa_key_pair()
        _save_key_pair(private_key_path, public_key_path, private_key,
                       public_key)
    else:
        # FIXME(skypilot): ran into failing this assert once, but forgot the
        # reproduction (has private key; but has not generated public key).
        #   AssertionError: /home/ubuntu/.ssh/sky-key.pub
        assert os.path.exists(public_key_path), (
            'Private key found, but associated public key '
            f'{public_key_path} does not exist.')
    return private_key_path, public_key_path


def _replace_ssh_info_in_config(config: Dict[str, Any],
                                public_key: str) -> Dict[str, Any]:
    config_str = common_utils.dump_yaml_str(config)
    config_str = config_str.replace('skypilot:ssh_user',
                                    config['auth']['ssh_user'])
    config_str = config_str.replace('skypilot:ssh_public_key_content',
                                    public_key)
    config = yaml.safe_load(config_str)
    return config


def setup_aws_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    _, public_key_path = get_or_generate_keys()
    with open(public_key_path, 'r') as f:
        public_key = f.read()
    config = _replace_ssh_info_in_config(config, public_key)
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
    with open(public_key_path, 'r') as f:
        public_key = f.read()
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
            sky_backup_config_path = os.path.expanduser(
                clouds.gcp.GCP_CONFIG_SKY_BACKUP_PATH)
            assert os.path.exists(sky_backup_config_path), (
                'GCP credential backup file '
                f'{sky_backup_config_path!r} does not exist.')

            with open(sky_backup_config_path, 'r') as infile:
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
    return _replace_ssh_info_in_config(config, public_key)


def setup_azure_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    _, public_key_path = get_or_generate_keys()
    with open(public_key_path, 'r') as f:
        public_key = f.read()
    return _replace_ssh_info_in_config(config, public_key)


def setup_lambda_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    get_or_generate_keys()

    # Ensure ssh key is registered with Lambda Cloud
    lambda_client = lambda_utils.LambdaCloudClient()
    public_key_path = os.path.expanduser(PUBLIC_SSH_KEY_PATH)
    with open(public_key_path, 'r') as f:
        public_key = f.read()
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
        ssh_key_data = file.read()
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


# Apr, 2023 by Hysun(hysun.he@oracle.com): Added support for OCI
def setup_oci_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    _, public_key_path = get_or_generate_keys()
    with open(public_key_path, 'r') as f:
        public_key = f.read()

    return _replace_ssh_info_in_config(config, public_key)


def setup_scp_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    _, public_key_path = get_or_generate_keys()
    with open(public_key_path, 'r') as f:
        public_key = f.read()
    return _replace_ssh_info_in_config(config, public_key)
