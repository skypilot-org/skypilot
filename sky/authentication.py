"""Module to enable a single SkyPilot key for all VMs in each cloud."""
import copy
import functools
import os
import re
import socket
import subprocess
import sys
import textwrap
import time
from typing import Any, Dict, Tuple

import colorama
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

from sky import clouds
from sky import sky_logging
from sky.adaptors import gcp
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
    """Returns the aboslute public and private key paths."""
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


def setup_aws_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    _, public_key_path = get_or_generate_keys()
    with open(public_key_path, 'r') as f:
        public_key = f.read()
    # Use cloud init in UserData to set up the authorized_keys to get
    # around the number of keys limit and permission issues with
    # ec2.describe_key_pairs.
    # Note that sudo and shell need to be specified to ensure setup works.
    # Reference: https://cloudinit.readthedocs.io/en/latest/reference/modules.html#users-and-groups  # pylint: disable=line-too-long
    for node_type in config['available_node_types']:
        config['available_node_types'][node_type]['node_config']['UserData'] = (
            textwrap.dedent(f"""\
            #cloud-config
            users:
            - name: {config['auth']['ssh_user']}
              shell: /bin/bash
              sudo: ALL=(ALL) NOPASSWD:ALL
              ssh-authorized-keys:
                - {public_key}
            """))
    return config


# Reference:
# https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/gcp/config.py
def _wait_for_compute_global_operation(project_name: str, operation_name: str,
                                       compute: Any) -> None:
    """Poll for global compute operation until finished."""
    logger.debug('wait_for_compute_global_operation: '
                 'Waiting for operation {} to finish...'.format(operation_name))
    max_polls = 10
    poll_interval = 5
    for _ in range(max_polls):
        result = compute.globalOperations().get(
            project=project_name,
            operation=operation_name,
        ).execute()
        if 'error' in result:
            raise Exception(result['error'])

        if result['status'] == 'DONE':
            logger.debug('wait_for_compute_global_operation: '
                         'Operation done.')
            break
        time.sleep(poll_interval)
    return result


# Snippets of code inspired from
# https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/gcp/config.py
# Takes in config, a yaml dict and outputs a postprocessed dict
# TODO(weilin): refactor the implementation to incorporate Ray autoscaler to
# avoid duplicated codes.
# Retry for the GCP as sometimes there will be connection reset by peer error.
@common_utils.retry
@gcp.import_package
def setup_gcp_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    private_key_path, public_key_path = get_or_generate_keys()
    config = copy.deepcopy(config)

    project_id = config['provider']['project_id']
    compute = gcp.build('compute',
                        'v1',
                        credentials=None,
                        cache_discovery=False)
    user = config['auth']['ssh_user']

    try:
        project = compute.projects().get(project=project_id).execute()
    except gcp.googleapiclient.errors.HttpError as e:
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

    project_oslogin = next(
        (item for item in project['commonInstanceMetadata'].get('items', [])
         if item['key'] == 'enable-oslogin'), {}).get('value', 'False')

    if project_oslogin.lower() == 'true':
        # project.
        logger.info(
            f'OS Login is enabled for GCP project {project_id}. Running '
            'additional authentication steps.')
        # Read the account information from the credential file, since the user
        # should be set according the account, when the oslogin is enabled.
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
                        'GCP authentication failed, as the oslogin is enabled '
                        f'but the file {config_path} does not contain the '
                        'account information.')
        config['auth']['ssh_user'] = account.replace('@', '_').replace('.', '_')

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
                                               proc.stderr)
        return config

    # OS Login is not enabled for the project. Add the ssh key directly to the
    # metadata.
    # TODO(zhwu): Use cloud init to add ssh public key, to avoid the permission
    # issue. A blocker is that the cloud init is not installed in the debian
    # image by default.
    project_keys = next(
        (item for item in project['commonInstanceMetadata'].get('items', [])
         if item['key'] == 'ssh-keys'), {}).get('value', '')
    ssh_keys = project_keys.split('\n') if project_keys else []

    # Get public key from file.
    with open(public_key_path, 'r') as f:
        public_key = f.read()

    # Check if ssh key in Google Project's metadata
    public_key_token = public_key.split(' ')[1]

    key_found = False
    for key in ssh_keys:
        key_list = key.split(' ')
        if len(key_list) != 3:
            continue
        if user == key_list[-1] and os.path.exists(
                private_key_path) and key_list[1] == public_key.split(' ')[1]:
            key_found = True

    if not key_found:
        new_ssh_key = '{user}:ssh-rsa {public_key_token} {user}'.format(
            user=user, public_key_token=public_key_token)
        metadata = project['commonInstanceMetadata'].get('items', [])

        ssh_key_index = [
            k for k, v in enumerate(metadata) if v['key'] == 'ssh-keys'
        ]
        assert len(ssh_key_index) <= 1

        if len(ssh_key_index) == 0:
            metadata.append({'key': 'ssh-keys', 'value': new_ssh_key})
        else:
            ssh_key_index = ssh_key_index[0]
            metadata[ssh_key_index]['value'] += '\n' + new_ssh_key

        project['commonInstanceMetadata']['items'] = metadata

        operation = compute.projects().setCommonInstanceMetadata(
            project=project['name'],
            body=project['commonInstanceMetadata']).execute()
        _wait_for_compute_global_operation(project['name'], operation['name'],
                                           compute)
    return config


def setup_azure_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    get_or_generate_keys()
    # Need to use ~ relative path because Ray uses the same
    # path for finding the public key path on both local and head node.
    config['auth']['ssh_public_key'] = PUBLIC_SSH_KEY_PATH

    file_mounts = config['file_mounts']
    file_mounts[PUBLIC_SSH_KEY_PATH] = PUBLIC_SSH_KEY_PATH
    config['file_mounts'] = file_mounts

    return config


def setup_lambda_authentication(config: Dict[str, Any]) -> Dict[str, Any]:
    get_or_generate_keys()

    # Ensure ssh key is registered with Lambda Cloud
    lambda_client = lambda_utils.LambdaCloudClient()
    if lambda_client.ssh_key_name is None:
        public_key_path = os.path.expanduser(PUBLIC_SSH_KEY_PATH)
        with open(public_key_path, 'r') as f:
            public_key = f.read()
        name = f'sky-key-{common_utils.get_user_hash()}'
        lambda_client.set_ssh_key(name, public_key)

    # Need to use ~ relative path because Ray uses the same
    # path for finding the public key path on both local and head node.
    config['auth']['ssh_public_key'] = PUBLIC_SSH_KEY_PATH

    file_mounts = config['file_mounts']
    file_mounts[PUBLIC_SSH_KEY_PATH] = PUBLIC_SSH_KEY_PATH
    config['file_mounts'] = file_mounts

    return config
