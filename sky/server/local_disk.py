"""Accounting for the API server's own local disk use.

The server owns several trees on local disk that grow with traffic rather
than with configuration: the per-request log files, the request debug logs,
and whatever the configured blob storage extracts locally for file mounts.
None of them has a size bound, and nothing inside the process knows how
large they have grown or how much room is left. The first component to
notice a filling disk is therefore the platform underneath -- on Kubernetes
the kubelet, whose only response is to evict the pod, after which the
evidence is gone.

This module measures those trees and the space left on the filesystems
holding them, so the growth is visible while there is still time to act.

Measurement only: nothing here refuses, caps or sheds work.

Two notes on matching what the platform sees:

* Sizes are counted in allocated blocks, not apparent size, and hard links
  are counted once, because that is what ``du`` reports and ``du`` is what
  the kubelet's ephemeral-storage accounting runs.
* A file that has been unlinked while a process still holds it open keeps
  its blocks but is invisible to any directory walk, so a leaked descriptor
  makes these numbers an undercount.
"""
import dataclasses
import os
import time
from typing import Dict, List, Optional, Tuple

from sky import sky_logging
from sky.server import constants as server_constants
from sky.server.blob import blob_storage
from sky.skylet import runtime_utils

logger = sky_logging.init_logger(__name__)

# Downward-API environment variables carrying the container's own
# ephemeral-storage resource fields, named after the existing
# SKYPILOT_POD_{CPU_CORE,MEMORY_BYTES}_LIMIT pair. Unset outside
# Kubernetes, and unset on Kubernetes unless the deployment declares the
# corresponding resource field -- a resourceFieldRef for an undeclared
# field resolves to the node's allocatable capacity, which would be
# reported as a budget this container does not actually have.
EPHEMERAL_STORAGE_LIMIT_ENV_VAR = 'SKYPILOT_POD_EPHEMERAL_STORAGE_BYTES_LIMIT'
EPHEMERAL_STORAGE_REQUEST_ENV_VAR = (
    'SKYPILOT_POD_EPHEMERAL_STORAGE_BYTES_REQUEST')

# Work bounds for one scan of one root. The scan is on the metrics refresh
# path, so it must finish in bounded time no matter how many files a root
# has accumulated or how slow the filesystem under it is. Exceeding either
# bound truncates that root's result and is reported, so a truncated scan
# is never mistaken for a small one.
DEFAULT_SCAN_TIMEOUT_SECONDS = 20.0
DEFAULT_SCAN_MAX_ENTRIES = 2_000_000

# How often the deadline is re-checked while draining a single directory.
_DEADLINE_CHECK_EVERY = 4096

# Bytes per st_blocks unit, fixed by POSIX regardless of the filesystem's
# own block size.
_BLOCK_SIZE = 512


@dataclasses.dataclass
class RootUsage:
    """Disk used by one named tree the server writes to."""
    path: str
    used_bytes: int
    files: int
    truncated: bool


@dataclasses.dataclass
class FilesystemUsage:
    """Space on one filesystem hosting at least one measured root."""
    mountpoint: str
    size_bytes: int
    avail_bytes: int


@dataclasses.dataclass
class Snapshot:
    """One pass over every root the server owns."""
    roots: Dict[str, RootUsage]
    filesystems: Dict[str, FilesystemUsage]
    budget_bytes: Optional[int]
    duration_seconds: float

    @property
    def used_bytes(self) -> int:
        """Total across the measured roots.

        A lower bound on what the platform attributes to this container:
        the roots cover what the server writes, not the whole writable
        layer.
        """
        return sum(r.used_bytes for r in self.roots.values())

    @property
    def headroom_bytes(self) -> Optional[int]:
        """Room left against ``budget_bytes``, or None without a budget.

        An upper bound, for the reason ``used_bytes`` is a lower one.
        """
        if self.budget_bytes is None:
            return None
        return max(self.budget_bytes - self.used_bytes, 0)


def local_roots() -> Dict[str, str]:
    """Returns {name: absolute path} for the trees the server writes locally.

    Resolved on every call rather than at import time so SKY_RUNTIME_DIR /
    HOME are honored the way the owning modules resolve their own paths,
    and so a blob storage backend installed after import is included.

    Deliberately excludes ``~/sky_logs``: in multi-replica deployments it
    is on shared storage, where it does not count against this container's
    local disk and where a directory walk is prohibitively slow. Its
    filesystem is already reported by the sky_logs retention instruments.
    """
    roots = {
        'request_logs': runtime_utils.expanduser(
            server_constants.REQUEST_LOG_PATH_PREFIX),
        'request_debug_logs': sky_logging.DEBUG_LOG_DIR,
        'catalogs': runtime_utils.get_runtime_dir_path('.sky/catalogs'),
        'wheels': runtime_utils.get_runtime_dir_path('.sky/wheels'),
    }
    # The blob backend knows its own local layout; an implementation that
    # keeps blobs on shared storage reports nothing here.
    try:
        roots.update(blob_storage.get_blob_storage().local_disk_roots())
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Could not resolve blob storage local roots: {e}')
    return roots


def budget_bytes() -> Optional[int]:
    """Returns this container's ephemeral-storage budget, if it has one.

    Prefers the limit, which is the boundary that gets enforced, and falls
    back to the request, which is what the deployment declared it needs.
    Returns None when neither is exposed -- there is no way to read the
    field from the kernel, since ephemeral-storage is not a cgroup
    controller.
    """
    for env_var in (EPHEMERAL_STORAGE_LIMIT_ENV_VAR,
                    EPHEMERAL_STORAGE_REQUEST_ENV_VAR):
        raw = os.environ.get(env_var)
        if not raw:
            continue
        try:
            value = int(float(raw))
        except ValueError:
            logger.warning(f'Ignoring unparseable {env_var}={raw!r}')
            continue
        if value > 0:
            return value
    return None


def _entry_bytes(stat_result) -> int:
    blocks = getattr(stat_result, 'st_blocks', None)
    if blocks is None:
        # No block count on this platform; apparent size is the best
        # available approximation.
        return stat_result.st_size
    return blocks * _BLOCK_SIZE


def _scan_root(path: str, deadline: float, max_entries: int) -> RootUsage:
    """Walks *path*, counting allocated blocks and regular files."""
    used = 0
    files = 0
    entries = 0
    truncated = False
    # Only inodes with more than one link can be reached twice, so only
    # those need to be remembered.
    seen: set = set()
    try:
        used += _entry_bytes(os.stat(path))
    except OSError:
        return RootUsage(path=path, used_bytes=0, files=0, truncated=False)

    stack: List[str] = [path]
    while stack:
        current = stack.pop()
        try:
            scandir = os.scandir(current)
        except OSError as e:
            logger.debug(f'Skipping {current} while measuring {path}: {e}')
            continue
        with scandir:
            for entry in scandir:
                entries += 1
                if entries >= max_entries:
                    truncated = True
                    break
                if (entries % _DEADLINE_CHECK_EVERY == 0 and
                        time.monotonic() >= deadline):
                    truncated = True
                    break
                try:
                    stat_result = entry.stat(follow_symlinks=False)
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    # Raced with a GC or a rotation; the bytes are gone.
                    continue
                if stat_result.st_nlink > 1:
                    key = (stat_result.st_dev, stat_result.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                used += _entry_bytes(stat_result)
                if is_dir:
                    stack.append(entry.path)
                else:
                    files += 1
        if truncated:
            break
    if truncated:
        logger.info(f'Local disk scan of {path} truncated after '
                    f'{entries} entries; reported size is a lower bound')
    return RootUsage(path=path,
                     used_bytes=used,
                     files=files,
                     truncated=truncated)


def _mountpoint(path: str) -> Optional[str]:
    """Returns the mount point of the filesystem holding *path*."""
    try:
        device = os.stat(path).st_dev
    except OSError:
        return None
    current = os.path.abspath(path)
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            return current
        try:
            if os.stat(parent).st_dev != device:
                return current
        except OSError:
            return current
        current = parent


def _filesystem_usage(paths: List[str]) -> Dict[str, FilesystemUsage]:
    """Returns space stats for the distinct filesystems holding *paths*."""
    out: Dict[str, FilesystemUsage] = {}
    for path in paths:
        mountpoint = _mountpoint(path)
        if mountpoint is None or mountpoint in out:
            continue
        try:
            stats = os.statvfs(mountpoint)
        except OSError as e:
            logger.debug(f'Could not statvfs {mountpoint}: {e}')
            continue
        out[mountpoint] = FilesystemUsage(
            mountpoint=mountpoint,
            size_bytes=stats.f_blocks * stats.f_frsize,
            # f_bavail, not f_bfree: the space actually available to a
            # non-root writer, which is what fills up first.
            avail_bytes=stats.f_bavail * stats.f_frsize)
    return out


def scan(
    timeout_seconds: float = DEFAULT_SCAN_TIMEOUT_SECONDS,
    max_entries: int = DEFAULT_SCAN_MAX_ENTRIES,
) -> Snapshot:
    """Measures every local root, sharing one deadline across all of them."""
    started = time.monotonic()
    deadline = started + timeout_seconds
    roots: Dict[str, RootUsage] = {}
    measured: List[Tuple[str, str]] = []
    for name, path in local_roots().items():
        if not os.path.isdir(path):
            # Not in use in this deployment; emitting a 0 would read as a
            # measured emptiness.
            continue
        measured.append((name, path))
        roots[name] = _scan_root(path, deadline, max_entries)
    return Snapshot(roots=roots,
                    filesystems=_filesystem_usage(
                        [path for _, path in measured]),
                    budget_bytes=budget_bytes(),
                    duration_seconds=time.monotonic() - started)
