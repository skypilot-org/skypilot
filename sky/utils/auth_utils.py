"""Utils for managing SkyPilot SSH key pairs."""

import functools
import os
from typing import Tuple

import filelock

from sky import global_user_state
from sky import sky_logging
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

MAX_TRIALS = 64
# TODO(zhwu): Support user specified key pair.
# We intentionally not have the ssh key pair to be stored in
# ~/.sky/api_server/clients, i.e. sky.server.common.API_SERVER_CLIENT_DIR,
# because ssh key pair need to persist across API server restarts, while
# the former dir is ephemeral.
_SSH_KEY_PATH_PREFIX = '~/.sky/clients/{user_hash}/ssh'


def get_ssh_key_and_lock_path(user_hash: str) -> Tuple[str, str, str]:
    user_ssh_key_prefix = _SSH_KEY_PATH_PREFIX.format(user_hash=user_hash)

    os.makedirs(os.path.expanduser(user_ssh_key_prefix),
                exist_ok=True,
                mode=0o700)
    private_key_path = os.path.join(user_ssh_key_prefix, 'sky-key')
    public_key_path = os.path.join(user_ssh_key_prefix, 'sky-key.pub')
    lock_path = os.path.join(user_ssh_key_prefix, '.__internal-sky-key.lock')
    return private_key_path, public_key_path, lock_path


def _generate_rsa_key_pair() -> Tuple[str, str]:
    # Keep the import of the cryptography local to avoid expensive
    # third-party imports when not needed.
    # pylint: disable=import-outside-toplevel
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

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
    """Returns the absolute private and public key paths."""
    user_hash = common_utils.get_user_hash()
    private_key_path, public_key_path, lock_path = get_ssh_key_and_lock_path(
        user_hash)
    private_key_path = os.path.expanduser(private_key_path)
    public_key_path = os.path.expanduser(public_key_path)
    lock_path = os.path.expanduser(lock_path)

    lock_dir = os.path.dirname(lock_path)
    # We should have the folder ~/.sky/generated/ssh to have 0o700 permission,
    # as the ssh configs will be written to this folder as well in
    # backend_utils.SSHConfigHelper
    os.makedirs(lock_dir, exist_ok=True, mode=0o700)
    with filelock.FileLock(lock_path, timeout=10):
        if not os.path.exists(private_key_path):
            ssh_public_key, ssh_private_key, exists = (
                global_user_state.get_ssh_keys(user_hash))
            if not exists:
                ssh_public_key, ssh_private_key = _generate_rsa_key_pair()
                global_user_state.set_ssh_keys(user_hash, ssh_public_key,
                                               ssh_private_key)
            _save_key_pair(private_key_path, public_key_path, ssh_private_key,
                           ssh_public_key)
    assert os.path.exists(public_key_path), (
        'Private key found, but associated public key '
        f'{public_key_path} does not exist.')
    return private_key_path, public_key_path


def create_ssh_key_files_from_db(private_key_path: str) -> bool:
    """Creates the ssh key files from the database.

    Returns:
        True if the ssh key files are created successfully, False otherwise.
    """
    # Assume private key path is in the format of
    # ~/.sky/clients/<user_hash>/ssh/sky-key
    separated_path = os.path.normpath(private_key_path).split(os.path.sep)
    assert separated_path[-1] == 'sky-key'
    assert separated_path[-2] == 'ssh'
    user_hash = separated_path[-3]

    private_key_path_generated, public_key_path, lock_path = (
        get_ssh_key_and_lock_path(user_hash))
    assert private_key_path == os.path.expanduser(private_key_path_generated), (
        f'Private key path {private_key_path} does not '
        'match the generated path '
        f'{os.path.expanduser(private_key_path_generated)}')
    private_key_path = os.path.expanduser(private_key_path)
    public_key_path = os.path.expanduser(public_key_path)
    lock_path = os.path.expanduser(lock_path)
    lock_dir = os.path.dirname(lock_path)

    if os.path.exists(private_key_path) and os.path.exists(public_key_path):
        return True
    # We should have the folder ~/.sky/generated/ssh to have 0o700 permission,
    # as the ssh configs will be written to this folder as well in
    # backend_utils.SSHConfigHelper
    os.makedirs(lock_dir, exist_ok=True, mode=0o700)
    with filelock.FileLock(lock_path, timeout=10):
        if not os.path.exists(private_key_path):
            ssh_public_key, ssh_private_key, exists = (
                global_user_state.get_ssh_keys(user_hash))
            if not exists:
                logger.debug(f'SSH keys not found for user {user_hash}')
                return False
            _save_key_pair(private_key_path, public_key_path, ssh_private_key,
                           ssh_public_key)
    assert os.path.exists(public_key_path), (
        'Private key found, but associated public key '
        f'{public_key_path} does not exist.')
    return True
