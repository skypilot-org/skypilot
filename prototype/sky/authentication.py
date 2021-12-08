"""Module to enable a single Sky key for all Sky VMs in each cloud."""
import boto3
import copy
import os
import pathlib
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from functools import partial
from googleapiclient import discovery
# TODO: Should tolerate if gcloud is not installed. Also,
# https://pypi.org/project/google-api-python-client/ recommends
# using Cloud Client Libraries for Python, where possible, for new code
# development.


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
            opener=partial(os.open, mode=0o600),
    ) as f:
        f.write(private_key)

    with open(public_key_path, 'w') as f:
        f.write(public_key)


def get_public_key_path(private_key_path):
    if private_key_path.endswith('.pem'):
        private_key_path, _ = private_key_path.rsplit('.', 1)
    return private_key_path + '.pub'


# Snippets of code inspired from
# https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/aws/config.py
# Takes in config, a yaml dict and outputs a postprocessed dict
def setup_aws_authentication(config):
    config = copy.deepcopy(config)
    private_key_path = config['auth'].get('ssh_private_key', None)
    # Defeault to ~/.ssh/sky-key
    if private_key_path is None:
        private_key_path = '~/.ssh/sky-key'
        config['auth']['ssh_private_key'] = private_key_path

    private_key_path = os.path.expanduser(private_key_path)
    public_key_path = get_public_key_path(private_key_path)
    key_name = private_key_path.split('/')[-1]

    # Generating ssh key if it does not exist
    if private_key_path is None or not os.path.exists(private_key_path):
        public_key, private_key = generate_rsa_key_pair()
        save_key_pair(private_key_path, public_key_path, private_key,
                      public_key)
    else:
        assert os.path.exists(public_key_path)
        public_key = open(public_key_path, 'rb').read().decode('utf-8')
        private_key = None

    ec2 = boto3.resource('ec2', config['provider']['region'])
    key_found = False
    for key in ec2.key_pairs.filter(Filters=[{
            'Name': 'key-name',
            'Values': [key_name]
    }]):
        if key.name == key_name:
            key_found = True

    if not key_found:
        ec2.import_key_pair(KeyName=key_name, PublicKeyMaterial=public_key)

    node_types = config['available_node_types']

    for node_type in node_types.values():
        node_type['node_config']['KeyName'] = key_name
    return config


# Snippets of code inspired from
# https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/aws/config.py
# Takes in config, a yaml dict and outputs a postprocessed dict
def setup_gcp_authentication(config):
    config = copy.deepcopy(config)
    private_key_path = config['auth'].get('ssh_private_key', None)
    # Defeault to ~/.ssh/sky-key
    if private_key_path is None:
        private_key_path = '~/.ssh/sky-key'
        config['auth']['ssh_private_key'] = private_key_path

    private_key_path = os.path.expanduser(private_key_path)
    public_key_path = get_public_key_path(private_key_path)
    config = copy.deepcopy(config)

    project_id = config['provider']['project_id']
    compute = discovery.build('compute',
                              'v1',
                              credentials=None,
                              cache_discovery=False)
    user = config['auth']['ssh_user']
    project = compute.projects().get(project=project_id).execute()
    project_keys = next(
        (item for item in project['commonInstanceMetadata'].get('items', [])
         if item['key'] == 'ssh-keys'), {}).get('value', '')
    ssh_keys = project_keys.split('\n') if project_keys else []

    # Generating ssh key if it does not exist
    if private_key_path is None or not os.path.exists(private_key_path):
        public_key, private_key = generate_rsa_key_pair()
        save_key_pair(private_key_path, public_key_path, private_key,
                      public_key)
    else:
        assert os.path.exists(public_key_path)
        public_key = open(public_key_path, 'rb').read().decode('utf-8')
        private_key = None

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
            ssh_dict = metadata[ssh_key_index]
            ssh_dict['value'] += '\n' + new_ssh_key
            compute.projects().setCommonInstanceMetadata(
                project=project['name'],
                body=project['commonInstanceMetadata']).execute()
            time.sleep(5)
    return config


def _unexpand_user(path):
    """Inverse of `os.path.expanduser`."""
    return ('~' / pathlib.Path(path).relative_to(pathlib.Path.home())).as_posix()


# Takes in config, a yaml dict and outputs a postprocessed dict
def setup_azure_authentication(config):
    # Doesn't need special library calls!
    config = copy.deepcopy(config)
    private_key_path = config['auth'].get('ssh_private_key', None)
    # Defeault to ~/.ssh/sky-key
    if private_key_path is None:
        private_key_path = '~/.ssh/sky-key'
        config['auth']['ssh_private_key'] = private_key_path

    private_key_path = os.path.expanduser(private_key_path)
    public_key_path = get_public_key_path(private_key_path)
    # Need to convert /Users/<username> back to ~ because Ray uses the same
    # path for finding the public key path on both local and head node.
    public_key_path = _unexpand_user(public_key_path)
    config['auth']['ssh_public_key'] = public_key_path

    file_mounts = config['file_mounts']
    file_mounts[public_key_path] = public_key_path
    config['file_mounts'] = file_mounts

    return config
