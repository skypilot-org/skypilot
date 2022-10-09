"""Module to enable a single SkyPilot key for all VMs in each cloud."""
import copy
import functools
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import time
import uuid

import colorama
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

from sky import clouds
from sky import sky_logging
from sky.adaptors import aws, gcp
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

colorama.init()
logger = sky_logging.init_logger(__name__)

# TODO: Should tolerate if gcloud is not installed. Also,
# https://pypi.org/project/google-api-python-client/ recommends
# using Cloud Client Libraries for Python, where possible, for new code
# development.

MAX_TRIALS = 64
PRIVATE_SSH_KEY_PATH = '~/.ssh/sky-key'


def generate_rsa_key_pair():
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


def save_key_pair(private_key_path, public_key_path, private_key, public_key):
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


def get_public_key_path(private_key_path):
    if private_key_path.endswith('.pem'):
        private_key_path, _ = private_key_path.rsplit('.', 1)
    return private_key_path + '.pub'


def get_or_generate_keys(private_key_path: str, public_key_path: str):
    """Returns private and public keys from the given paths.

    If the private_key_path is not provided or does not exist, then a new
    keypair is generated and written to the path.
    """
    if private_key_path is None or not os.path.exists(private_key_path):
        public_key, private_key = generate_rsa_key_pair()
        save_key_pair(private_key_path, public_key_path, private_key,
                      public_key)
    else:
        assert os.path.exists(public_key_path)
        public_key = open(public_key_path, 'rb').read().decode('utf-8')
        private_key = open(private_key_path, 'rb').read().decode('utf-8')
    return private_key, public_key


# Snippets of code inspired from
# https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/aws/config.py
# Takes in config, a yaml dict and outputs a postprocessed dict
def setup_aws_authentication(config):
    from Crypto.PublicKey import RSA  # pylint: disable=import-outside-toplevel
    config = copy.deepcopy(config)
    private_key_path = config['auth'].get('ssh_private_key', None)
    if private_key_path is None:
        private_key_path = PRIVATE_SSH_KEY_PATH
        config['auth']['ssh_private_key'] = private_key_path

    private_key_path = os.path.expanduser(private_key_path)
    public_key_path = get_public_key_path(private_key_path)

    # Generating ssh key if it does not exist
    _, public_key = get_or_generate_keys(private_key_path, public_key_path)

    ec2 = aws.client('ec2', region_name=config['provider']['region'])
    key_pairs = ec2.describe_key_pairs()['KeyPairs']
    key_name = None
    all_key_names = set()

    def _get_fingerprint(public_key_path):
        key = RSA.importKey(open(public_key_path).read())

        def insert_char_every_n_chars(string, char='\n', every=2):
            return char.join(
                string[i:i + every] for i in range(0, len(string), every))

        md5digest = hashlib.md5(key.exportKey('DER', pkcs=8)).hexdigest()
        fingerprint = insert_char_every_n_chars(md5digest, ':', 2)
        return fingerprint

    for key in key_pairs:
        # Compute Fingerprint of public key
        aws_fingerprint = key['KeyFingerprint']
        local_fingerprint = _get_fingerprint(public_key_path)
        if aws_fingerprint == local_fingerprint:
            key_name = key['KeyName']
        # Add key name to key name list
        all_key_names.add(key['KeyName'])

    if key_name is None:
        for fail_counter in range(MAX_TRIALS):
            key_name = 'sky-key-' + uuid.uuid4().hex[:6]
            if key_name not in all_key_names:
                ec2.import_key_pair(KeyName=key_name,
                                    PublicKeyMaterial=public_key)
                break
        if fail_counter == MAX_TRIALS - 1:
            raise RuntimeError(
                'Failed to generate a unique key pair ID for AWS')

    node_types = config['available_node_types']

    for node_type in node_types.values():
        node_type['node_config']['KeyName'] = key_name
    return config


# Reference:
# https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/gcp/config.py
def _wait_for_compute_global_operation(project_name, operation_name, compute):
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
def setup_gcp_authentication(config):
    config = copy.deepcopy(config)
    private_key_path = config['auth'].get('ssh_private_key', None)
    if private_key_path is None:
        private_key_path = PRIVATE_SSH_KEY_PATH
        config['auth']['ssh_private_key'] = private_key_path

    private_key_path = os.path.expanduser(private_key_path)
    public_key_path = get_public_key_path(private_key_path)
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
            logger.error(f'{yellow}Enable them by running:{reset} '
                         f'{bright}sky check{reset}')
            logger.error('Details:')
            logger.error(f'{dim}{match.group(1)}{reset}\n'
                         f'{dim}    {match.group(2)}{reset}\n'
                         f'{dim}{match.group(3)}{reset}')
            sys.exit(1)
        else:
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

        # Generating ssh key if it does not exist
        get_or_generate_keys(private_key_path, public_key_path)

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
    project_keys = next(
        (item for item in project['commonInstanceMetadata'].get('items', [])
         if item['key'] == 'ssh-keys'), {}).get('value', '')
    ssh_keys = project_keys.split('\n') if project_keys else []

    # Generating ssh key if it does not exist
    _, public_key = get_or_generate_keys(private_key_path, public_key_path)

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


def _unexpand_user(path):
    """Inverse of `os.path.expanduser`."""
    return ('~' /
            pathlib.Path(path).relative_to(pathlib.Path.home())).as_posix()


# Takes in config, a yaml dict and outputs a postprocessed dict
def setup_azure_authentication(config):
    # Doesn't need special library calls!
    config = copy.deepcopy(config)
    private_key_path = config['auth'].get('ssh_private_key', None)
    if private_key_path is None:
        private_key_path = PRIVATE_SSH_KEY_PATH
        config['auth']['ssh_private_key'] = private_key_path

    private_key_path = os.path.expanduser(private_key_path)
    public_key_path = get_public_key_path(private_key_path)

    # Generating ssh key if it does not exist
    _, _ = get_or_generate_keys(private_key_path, public_key_path)

    # Need to convert /Users/<username> back to ~ because Ray uses the same
    # path for finding the public key path on both local and head node.
    public_key_path = _unexpand_user(public_key_path)
    config['auth']['ssh_public_key'] = public_key_path

    file_mounts = config['file_mounts']
    file_mounts[public_key_path] = public_key_path
    config['file_mounts'] = file_mounts

    return config
