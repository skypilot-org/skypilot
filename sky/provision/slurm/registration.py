"""Remote registration of Slurm clusters.

In client-server mode nothing can *register* a Slurm cluster remotely: the
``~/.slurm/config`` file (an OpenSSH ``ssh_config``-format file) and the
per-cluster identity / ``known_hosts`` files live only on the API server's
filesystem, and the only slurm HTTP endpoints upstream are read-only. This
module provides the read-modify-write core behind the ``/slurm_clusters`` CRUD
router so an admin control plane (e.g. a multi-tenant enrollment flow) can
register clusters over HTTP instead of writing daemon-local files by hand.

The ``~/.slurm/config`` file is parsed with ``paramiko.config.SSHConfig`` (see
``sky.provision.slurm.utils``) which has no writer, so this module renders
``Host`` blocks itself. Blocks it manages are wrapped in sentinel markers
(``# BEGIN skypilot-managed <name>`` / ``# END skypilot-managed <name>``); any
content outside those markers (e.g. hand-added hosts, ``Host *`` defaults) is
preserved verbatim on every write.

All writes are guarded by a distributed lock (``sky.utils.locks``) because
concurrent enrollments against a shared API server are the expected use case --
unlike ``SSHNodePoolManager``'s unlocked read-modify-write.
"""
import os
import re
from typing import Any, Dict, List, Optional

from paramiko.config import SSHConfig

from sky import sky_logging
from sky.provision.slurm import utils as slurm_utils
from sky.utils import locks

logger = sky_logging.init_logger(__name__)

# Directory (under the same parent as ~/.slurm/config) holding per-cluster
# identity files and known_hosts files written by the registration API.
_SLURM_DIR = os.path.dirname(slurm_utils.DEFAULT_SLURM_PATH)
KEYS_DIR = os.path.join(_SLURM_DIR, 'keys')
KNOWN_HOSTS_DIR = os.path.join(_SLURM_DIR, 'known_hosts')

# Lock guarding all rewrites of ~/.slurm/config. Matches the filelock family
# used for workspace config updates; auto-detects postgres when configured.
_SLURM_CONFIG_LOCK_ID = 'slurm-config-update'
_SLURM_CONFIG_LOCK_TIMEOUT_SECONDS = 60

# Sentinel markers wrapping blocks this module owns. Everything outside a
# BEGIN/END pair is preserved verbatim.
_BLOCK_BEGIN = '# BEGIN skypilot-managed {name}'
_BLOCK_END = '# END skypilot-managed {name}'

# A registered cluster name is used as a Host alias and as a filename for its
# identity / known_hosts files, so keep it to a safe charset.
_VALID_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')


def _validate_name(name: str) -> None:
    if not name or not _VALID_NAME_RE.match(name):
        raise ValueError(
            f'Invalid Slurm cluster name {name!r}: names may only contain '
            'letters, digits, and the characters ".", "_", and "-".')


class SlurmClusterManager:
    """Read-modify-write manager for ``~/.slurm/config`` and its key files."""

    def __init__(self) -> None:
        self.config_path = os.path.expanduser(slurm_utils.DEFAULT_SLURM_PATH)
        self.keys_dir = os.path.expanduser(KEYS_DIR)
        self.known_hosts_dir = os.path.expanduser(KNOWN_HOSTS_DIR)

    # ---- public read API ------------------------------------------------

    def list_clusters(self) -> Dict[str, Dict[str, Any]]:
        """Return registered clusters and their non-secret detail.

        Reads via paramiko so both managed and hand-added hosts are listed.
        Identity file *contents* and host keys are never returned -- only the
        connection detail needed to identify a cluster.
        """
        path = self.config_path
        if not os.path.exists(path):
            return {}
        ssh_config = SSHConfig.from_path(path)
        clusters: Dict[str, Dict[str, Any]] = {}
        for name in ssh_config.get_hostnames():
            if name == '*':
                continue
            resolved = ssh_config.lookup(name)
            clusters[name] = {
                'name': name,
                'host': resolved.get('hostname'),
                'user': resolved.get('user'),
                'port': int(resolved['port']) if 'port' in resolved else None,
                'proxy_jump': resolved.get('proxyjump'),
                'managed': self._is_managed(name),
            }
        return clusters

    # ---- public write API (lock-guarded) --------------------------------

    def register_cluster(
        self,
        name: str,
        host: str,
        user: str,
        identity_file: str,
        port: int = 22,
        host_key: Optional[str] = None,
        proxy_jump: Optional[str] = None,
        identities_only: bool = True,
    ) -> None:
        """Upsert a cluster block plus its identity / known_hosts files.

        Args:
            name: Cluster name, used as the ``Host`` alias.
            host: The ``HostName`` to connect to.
            user: SSH user.
            identity_file: Private key *contents* (not a path); stored 0600.
            port: SSH port.
            host_key: Optional ``known_hosts`` file *contents*. When provided,
                the block pins the host key with ``StrictHostKeyChecking yes``
                and a per-cluster ``UserKnownHostsFile``.
            proxy_jump: Optional ``ProxyJump`` value (e.g. a bastion alias).
            identities_only: Whether to set ``IdentitiesOnly yes``.
        """
        _validate_name(name)
        if not host:
            raise ValueError('`host` is required.')
        if not user:
            raise ValueError('`user` is required.')
        if not identity_file:
            raise ValueError('`identity_file` (key contents) is required.')

        with locks.get_lock(_SLURM_CONFIG_LOCK_ID,
                            _SLURM_CONFIG_LOCK_TIMEOUT_SECONDS):
            identity_path = self._write_identity_file(name, identity_file)
            known_hosts_path = None
            if host_key:
                known_hosts_path = self._write_known_hosts_file(name, host_key)

            block = self._render_block(
                name=name,
                host=host,
                user=user,
                port=port,
                identity_path=identity_path,
                known_hosts_path=known_hosts_path,
                proxy_jump=proxy_jump,
                identities_only=identities_only,
            )
            content = self._read_config()
            content = self._replace_block(content, name, block)
            self._write_config(content)

    def delete_cluster(self, name: str) -> bool:
        """Remove a managed cluster block and its identity / known_hosts files.

        Returns True if a managed block was removed, False if none existed.
        """
        _validate_name(name)
        with locks.get_lock(_SLURM_CONFIG_LOCK_ID,
                            _SLURM_CONFIG_LOCK_TIMEOUT_SECONDS):
            content = self._read_config()
            new_content = self._replace_block(content, name, block=None)
            removed = new_content != content
            if removed:
                self._write_config(new_content)
            # Best-effort cleanup of the sidecar files regardless.
            for path in (os.path.join(self.keys_dir, name),
                         os.path.join(self.known_hosts_dir, name)):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError as e:
                    logger.warning(f'Failed to remove {path}: {e}')
            return removed

    # ---- internals ------------------------------------------------------

    def _read_config(self) -> str:
        if not os.path.exists(self.config_path):
            return ''
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return f.read()

    def _write_config(self, content: str) -> None:
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        # Atomic replace so a concurrent reader never sees a half-written file.
        tmp_path = f'{self.config_path}.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp_path, self.config_path)

    def _write_identity_file(self, name: str, contents: str) -> str:
        os.makedirs(self.keys_dir, exist_ok=True)
        path = os.path.join(self.keys_dir, name)
        # Write then chmod 0600 (ssh refuses world-readable private keys).
        with open(path, 'w', encoding='utf-8') as f:
            f.write(contents)
            if not contents.endswith('\n'):
                f.write('\n')
        os.chmod(path, 0o600)
        return path

    def _write_known_hosts_file(self, name: str, contents: str) -> str:
        os.makedirs(self.known_hosts_dir, exist_ok=True)
        path = os.path.join(self.known_hosts_dir, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(contents)
            if not contents.endswith('\n'):
                f.write('\n')
        os.chmod(path, 0o600)
        return path

    def _is_managed(self, name: str) -> bool:
        begin = _BLOCK_BEGIN.format(name=name)
        return begin in self._read_config()

    @staticmethod
    def _render_block(name: str, host: str, user: str, port: int,
                      identity_path: str, known_hosts_path: Optional[str],
                      proxy_jump: Optional[str], identities_only: bool) -> str:
        lines = [
            _BLOCK_BEGIN.format(name=name),
            f'Host {name}',
            f'    HostName {host}',
            f'    User {user}',
            f'    Port {port}',
            f'    IdentityFile {identity_path}',
        ]
        if identities_only:
            lines.append('    IdentitiesOnly yes')
        if known_hosts_path is not None:
            # Pin the host key so a swapped/MITM'd host fails the connection.
            lines.append('    StrictHostKeyChecking yes')
            lines.append(f'    UserKnownHostsFile {known_hosts_path}')
        if proxy_jump:
            lines.append(f'    ProxyJump {proxy_jump}')
        lines.append(_BLOCK_END.format(name=name))
        return '\n'.join(lines) + '\n'

    @staticmethod
    def _replace_block(content: str, name: str, block: Optional[str]) -> str:
        """Replace (or delete, when block is None) the managed block for name.

        Content outside the BEGIN/END markers is preserved verbatim. When no
        managed block exists and ``block`` is given, it is appended.
        """
        begin = re.escape(_BLOCK_BEGIN.format(name=name))
        end = re.escape(_BLOCK_END.format(name=name))
        # Match the marker pair and any trailing newline after END.
        pattern = re.compile(rf'{begin}\n.*?{end}\n?', re.DOTALL)
        if pattern.search(content):
            replacement = block if block is not None else ''
            return pattern.sub(lambda _: replacement or '', content, count=1)
        # No existing managed block.
        if block is None:
            return content
        if content and not content.endswith('\n'):
            content += '\n'
        # Separate blocks with a blank line for readability.
        if content:
            content += '\n'
        return content + block


# Module-level convenience wrappers (mirrors ssh_node_pools.core).


def list_clusters() -> Dict[str, Dict[str, Any]]:
    return SlurmClusterManager().list_clusters()


def register_cluster(name: str,
                     host: str,
                     user: str,
                     identity_file: str,
                     port: int = 22,
                     host_key: Optional[str] = None,
                     proxy_jump: Optional[str] = None,
                     identities_only: bool = True) -> None:
    SlurmClusterManager().register_cluster(name=name,
                                           host=host,
                                           user=user,
                                           identity_file=identity_file,
                                           port=port,
                                           host_key=host_key,
                                           proxy_jump=proxy_jump,
                                           identities_only=identities_only)


def delete_cluster(name: str) -> bool:
    return SlurmClusterManager().delete_cluster(name)


def registered_cluster_names() -> List[str]:
    return list(SlurmClusterManager().list_clusters().keys())
