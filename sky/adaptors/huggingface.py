"""Hugging Face adapter for Bucket storage."""
# pylint: disable=import-outside-toplevel
import os
import stat
from typing import Dict, Optional, Tuple

from sky import exceptions
from sky.adaptors import common
from sky.clouds import cloud
from sky.utils import annotations
from sky.utils import ux_utils

NAME = 'HuggingFace'

# Base prefix for all Hugging Face Hub URLs (buckets + repos).
HF_URL_PREFIX = 'hf://'
# URL prefix specific to HF Buckets.
# See: https://huggingface.co/docs/huggingface_hub/guides/buckets
HF_BUCKETS_URL_PREFIX = 'hf://buckets/'
# URL prefixes for read-only HF repo mounts via ``hf-mount``.
# Models: ``hf://<owner>/<model>``; datasets: ``hf://datasets/...``;
# spaces: ``hf://spaces/...``.
HF_DATASETS_URL_PREFIX = 'hf://datasets/'
HF_SPACES_URL_PREFIX = 'hf://spaces/'

# Where the `hf` CLI / `huggingface_hub` stores the user token. We mount this
# file into remote clusters so that `hf-mount` and `huggingface_hub` can
# authenticate transparently.
# https://huggingface.co/docs/huggingface_hub/en/package_reference/authentication
HF_TOKEN_PATH = '~/.cache/huggingface/token'
# A legacy location used by older ``huggingface-cli`` versions.
HF_TOKEN_PATH_LEGACY = '~/.huggingface/token'
HF_TOKEN_PATH_ENV_CACHE = '~/.sky/huggingface/token'

_INDENT_PREFIX = '    '
_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Hugging Face. '
                         'Try pip install "skypilot[huggingface]"')

# `huggingface_hub` is moderately heavy to import (several 100 ms) and is only
# needed when HF Bucket storage is actually used. Use LazyImport so that the
# cost is deferred from `import sky`.
huggingface_hub = common.LazyImport('huggingface_hub',
                                    import_error_message=_IMPORT_ERROR_MESSAGE)
_LAZY_MODULES = (huggingface_hub,)


@annotations.lru_cache(scope='global')
def api():
    """Returns a cached ``HfApi`` instance."""
    # Import here rather than top-level so ``sky`` import stays cheap.
    from huggingface_hub import HfApi  # pylint: disable=import-outside-toplevel
    return HfApi()


def get_token() -> Optional[str]:
    """Returns the user's Hugging Face token, or None if unauthenticated.

    Tries, in order: the ``HF_TOKEN`` / ``HUGGING_FACE_HUB_TOKEN`` environment
    variables, then the token file written by ``hf auth login`` /
    ``huggingface-cli login``.
    """
    env_token = (os.environ.get('HF_TOKEN') or
                 os.environ.get('HUGGING_FACE_HUB_TOKEN'))
    if env_token:
        return env_token.strip() or None
    for candidate in (HF_TOKEN_PATH, HF_TOKEN_PATH_LEGACY):
        path = os.path.expanduser(candidate)
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    token = f.read().strip()
                if token:
                    return token
            except OSError:
                continue
    return None


@common.load_lazy_modules(_LAZY_MODULES)
def hf_hub_errors():
    """Returns the ``huggingface_hub.errors`` module."""
    from huggingface_hub import errors as hf_errors
    return hf_errors


def check_credentials(
        cloud_capability: cloud.CloudCapability) -> Tuple[bool, Optional[str]]:
    if cloud_capability == cloud.CloudCapability.STORAGE:
        return check_storage_credentials()
    raise exceptions.NotSupportedError(
        f'{NAME} does not support {cloud_capability}.')


def check_storage_credentials() -> Tuple[bool, Optional[str]]:
    """Checks if the user is authenticated against the Hugging Face Hub.

    Returns:
        ``(True, None)`` if the user has a usable token, otherwise
        ``(False, hint)`` where ``hint`` is a user-facing message explaining
        how to authenticate.
    """
    hints = None

    # Make sure huggingface_hub itself is importable before we probe auth, so
    # we can give a clean "install skypilot[huggingface]" hint.
    try:
        huggingface_hub.load_module()
    except ImportError as e:
        return False, str(e)

    token = get_token()
    if not token:
        hints = ('Hugging Face token is not set. '
                 'Set the HF_TOKEN environment variable or run '
                 '`hf auth login` to authenticate.')
        hints += f'\n{_INDENT_PREFIX}$ pip install "skypilot[huggingface]"'
        hints += (f'\n{_INDENT_PREFIX}$ hf auth login   '
                  '# or: export HF_TOKEN=<your-token>')
        return False, hints

    # If we have a token, verify it actually works.
    try:
        user = api().whoami(token=token)
    except Exception as e:  # pylint: disable=broad-except
        hints = (f'Failed to validate Hugging Face credentials: {e}. '
                 'Re-run `hf auth login` to refresh your token.')
        return False, hints

    # ``whoami`` returns a dict with at least a ``name`` key.
    if isinstance(user, dict) and user.get('name'):
        return True, None
    with ux_utils.print_exception_no_traceback():
        return False, ('Hugging Face credentials appear invalid '
                       '(empty whoami response).')


def get_credential_file_mounts() -> Dict[str, str]:
    """Returns credential file mounts for Hugging Face.

    The token file is uploaded so that ``huggingface_hub`` and ``hf-mount`` on
    the remote cluster can authenticate without needing the token to be baked
    into the task YAML.
    """
    mounts: Dict[str, str] = {}
    canonical_path = os.path.expanduser(HF_TOKEN_PATH)
    legacy_path = os.path.expanduser(HF_TOKEN_PATH_LEGACY)
    if os.path.exists(canonical_path):
        mounts[HF_TOKEN_PATH] = HF_TOKEN_PATH
        return mounts
    if os.path.exists(legacy_path):
        mounts[HF_TOKEN_PATH] = HF_TOKEN_PATH_LEGACY
        return mounts

    env_token = (os.environ.get('HF_TOKEN') or
                 os.environ.get('HUGGING_FACE_HUB_TOKEN'))
    if env_token and env_token.strip():
        cache_path = os.path.expanduser(HF_TOKEN_PATH_ENV_CACHE)
        cache_dir = os.path.dirname(cache_path)
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        # ``os.makedirs`` with ``exist_ok=True`` ignores ``mode`` for
        # pre-existing dirs, so tighten perms explicitly and verify the
        # final mode before writing the token. We refuse to write if the
        # dir is group- or world-accessible.
        try:
            os.chmod(cache_dir, 0o700)
        except OSError as e:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageSpecError(
                    f'Failed to secure HF token cache directory '
                    f'{cache_dir!r} (could not set mode 0o700): {e}') from e
        actual_mode = stat.S_IMODE(os.stat(cache_dir).st_mode)
        if actual_mode & 0o077:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageSpecError(
                    f'HF token cache directory {cache_dir!r} is group- '
                    f'or world-accessible (mode={oct(actual_mode)}); '
                    f'refusing to write token there. Tighten perms with: '
                    f'chmod 700 {cache_dir!r}')
        # Create the file with 0o600 atomically so we never expose the token
        # to other users, not even for the brief window between ``open`` and
        # ``chmod``.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, 'O_NOFOLLOW'):
            flags |= os.O_NOFOLLOW
        fd = os.open(cache_path, flags, stat.S_IRUSR | stat.S_IWUSR)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(env_token.strip())
        except Exception:
            # ``os.fdopen`` owns the fd on success; on failure before that
            # takes effect we need to close it ourselves.
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        mounts[HF_TOKEN_PATH] = HF_TOKEN_PATH_ENV_CACHE
    return mounts
