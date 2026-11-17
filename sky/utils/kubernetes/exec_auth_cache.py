"""Share `aws eks get-token` results across processes on the API server.

Every kubectl the API server starts runs its kubeconfig's exec plugin, and
client-go keeps the returned credential only in process memory. With an EKS
kubeconfig that is one `aws eks get-token` per kubectl: a Python interpreter
start plus a call to the credential provider, for a token that stays valid
for about 15 minutes.

`install` puts an `aws` front (aws_exec_auth_cache.sh) first on the server's
PATH. Kubeconfigs on the API server name a bare `aws` exec command (see
exec_kubeconfig_converter.py), so kubectl, `kubectl port-forward` and the
Python Kubernetes client all resolve to the front, which serves get-token
from a cache in a private directory on local disk and runs the real CLI for
everything else.
"""
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
from typing import Optional

from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

_SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'aws_exec_auth_cache.sh')
_REQUIRED_TOOLS = ('flock', 'sha256sum', 'date', 'mv', 'mkdir', 'rm')
# bash >= 5.0 for EPOCHSECONDS; GNU date for parsing expirationTimestamp.
_PREREQUISITE_CHECK = ('[[ -n "$EPOCHSECONDS" ]] && '
                       '[[ "$(date -u -d 1970-01-01T00:01:00Z +%s)" == 60 ]]')


def cache_root() -> str:
    """Directory holding the front's bin/ and cache/ (local disk)."""
    return os.path.join(tempfile.gettempdir(),
                        f'skypilot-exec-auth-{os.getuid()}')


def bin_dir() -> str:
    return os.path.join(cache_root(), 'bin')


def _ensure_private_dir(path: str) -> bool:
    """Create `path` as 0700, or verify an existing one is ours and 0700."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(path)
    return (stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and
            stat.S_IMODE(st.st_mode) & 0o077 == 0)


def _prerequisites_met(bash: str, search_path: str) -> bool:
    if any(
            shutil.which(tool, path=search_path) is None
            for tool in _REQUIRED_TOOLS):
        return False
    env = dict(os.environ, PATH=search_path)
    result = subprocess.run([bash, '-c', _PREREQUISITE_CHECK],
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                            timeout=10)
    return result.returncode == 0


def _write_front(path: str, bash: str, real_aws: str, cache_dir: str) -> None:
    with open(_SCRIPT_PATH, 'r', encoding='utf-8') as f:
        script = f.read()
    if script.startswith('#!'):
        script = script.split('\n', 1)[1]
    content = (f'#!{bash}\n'
               f'REAL_AWS={shlex.quote(real_aws)}\n'
               f'CACHE_DIR={shlex.quote(cache_dir)}\n'
               f'{script}')
    common_utils.atomic_write_text(path, content, mode=0o700)


def install() -> Optional[str]:
    """Put the caching `aws` front first on PATH for this process tree.

    Must run before any worker or controller process is spawned. A no-op
    when disabled, when no `aws` CLI is on PATH, or when the host lacks the
    tools the front needs.

    Returns:
        The real `aws` path the front forwards to, or None if not installed.
    """
    if os.environ.get(constants.ENV_VAR_SERVER_EXEC_AUTH_CACHE, '1') == '0':
        return None
    front_dir = bin_dir()
    search_path = os.pathsep.join(
        p for p in os.environ.get('PATH', '').split(os.pathsep)
        if p and os.path.abspath(p) != front_dir)
    real_aws = shutil.which('aws', path=search_path)
    bash = shutil.which('bash', path=search_path)
    if real_aws is None or bash is None:
        return None
    try:
        if not _prerequisites_met(bash, search_path):
            logger.debug('Not caching aws eks get-token: missing bash >= 5, '
                         'flock, sha256sum or GNU date.')
            return None
        root = cache_root()
        if not (_ensure_private_dir(root) and _ensure_private_dir(front_dir)):
            logger.warning(f'Not caching aws eks get-token: {root} is not a '
                           'private directory owned by this user.')
            return None
        _write_front(os.path.join(front_dir, 'aws'), bash, real_aws,
                     os.path.join(root, 'cache'))
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f'Not caching aws eks get-token: {e}')
        return None
    os.environ['PATH'] = os.pathsep.join([front_dir, search_path])
    logger.info(f'Caching aws eks get-token for kubectl ({real_aws}).')
    return real_aws
