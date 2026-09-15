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

Three notes on matching what the platform sees:

* Sizes are counted in allocated blocks, not apparent size, and hard links
  are counted once, because that is what ``du`` reports and ``du`` is what
  the kubelet's ephemeral-storage accounting runs.
* A file that has been unlinked while a process still holds it open keeps
  its blocks but is invisible to any directory walk, so a leaked descriptor
  makes these numbers an undercount.
* Not every root is charged to the container. A deployment can mount a
  persistent volume partway into the tree -- the same directory is local in
  one layout and shared in another -- and those bytes belong to the volume's
  budget, not this container's. Each filesystem is classified, and only the
  charged roots are compared against the budget.
"""
import dataclasses
import errno
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

# Which resource field a budget came from. Only a limit is enforced: a
# platform is free to let a container exceed its request, so headroom
# against a request would reach zero with room to spare.
BUDGET_SOURCE_LIMIT = 'limit'
BUDGET_SOURCE_REQUEST = 'request'

# Work bounds for one scan of one root. The scan is on the metrics refresh
# path, so it must finish in bounded time no matter how many files a root
# has accumulated or how slow the filesystem under it is. Exceeding either
# bound truncates that root's result and is reported, so a truncated scan
# is never mistaken for a small one.
DEFAULT_SCAN_TIMEOUT_SECONDS = 20.0
DEFAULT_SCAN_MAX_ENTRIES = 2_000_000

# Bytes per st_blocks unit, fixed by POSIX regardless of the filesystem's
# own block size.
_BLOCK_SIZE = 512

# Substring identifying a Kubernetes local scratch volume in the source
# path a bind mount reports. Such a volume lives on the node's own disk
# and is charged to the pod's ephemeral storage, unlike a persistent
# volume mounted at the same kind of path.
_EPHEMERAL_VOLUME_MARKER = 'kubernetes.io~empty-dir'


@dataclasses.dataclass
class RootUsage:
    """Disk used by one named tree the server writes to."""
    path: str
    used_bytes: int
    files: int
    truncated: bool
    # Entries the walk could not read for a reason other than the entry
    # having gone away. Those bytes are missing from used_bytes, so a
    # non-zero count means the size is a lower bound.
    unreadable: int = 0
    # Mount point of the filesystem holding this root, so a root can be
    # joined to its entry in Snapshot.filesystems. None if it could not
    # be determined.
    mountpoint: Optional[str] = None


@dataclasses.dataclass
class FilesystemUsage:
    """Space on one filesystem hosting at least one measured root."""
    mountpoint: str
    size_bytes: int
    avail_bytes: int
    # Whether bytes written here count against the container's own
    # ephemeral-storage budget. See _charged_to_ephemeral().
    charged_to_ephemeral: bool


@dataclasses.dataclass
class Budget:
    """The container's declared ephemeral-storage allowance."""
    total_bytes: int
    # BUDGET_SOURCE_LIMIT or BUDGET_SOURCE_REQUEST.
    source: str

    @property
    def enforced(self) -> bool:
        return self.source == BUDGET_SOURCE_LIMIT


@dataclasses.dataclass
class Snapshot:
    """One pass over every root the server owns."""
    roots: Dict[str, RootUsage]
    filesystems: Dict[str, FilesystemUsage]
    budget: Optional[Budget]
    duration_seconds: float

    @property
    def used_bytes(self) -> int:
        """Total across the roots charged to this container.

        Roots on a filesystem the platform does not charge to this
        container -- a persistent volume, a memory-backed tmpfs -- are
        excluded, so this stays comparable to ``budget_bytes``. Still a
        lower bound: the roots cover what the server writes, not the whole
        writable layer.
        """
        return sum(usage.used_bytes
                   for name, usage in self.roots.items()
                   if self.is_charged_to_ephemeral(name))

    def is_charged_to_ephemeral(self, name: str) -> bool:
        """Whether root *name* counts against ``budget_bytes``."""
        root = self.roots.get(name)
        if root is None or root.mountpoint is None:
            return False
        filesystem = self.filesystems.get(root.mountpoint)
        return filesystem is not None and filesystem.charged_to_ephemeral

    @property
    def headroom_bytes(self) -> Optional[int]:
        """Room left before the budget stops the server writing.

        None unless the budget is an enforced one. Room left against a
        mere scheduling request is not headroom: the platform allows a
        container past its request, so the number would reach zero while
        the disk still has space, and read as imminent death. The exact
        answer for the boundary that does stop writes is the available
        space on the filesystem -- see ``filesystems``.

        An upper bound, for the reason ``used_bytes`` is a lower one.
        """
        if self.budget is None or not self.budget.enforced:
            return None
        return max(self.budget.total_bytes - self.used_bytes, 0)


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
    return _drop_nested(roots)


def _drop_nested(roots: Dict[str, str]) -> Dict[str, str]:
    """Drops any root contained in another, keeping the outermost.

    The roots must be disjoint trees. Walking a tree twice would count
    every byte in it twice, and hard links are only deduplicated within a
    single root, so an overlap corrupts the total rather than merely
    duplicating a label. Enforced here so a backend that reports a
    directory already covered by another root cannot silently inflate the
    numbers.
    """
    normalized = {
        name: os.path.normpath(os.path.abspath(path))
        for name, path in roots.items()
    }
    kept: Dict[str, str] = {}
    for name, path in normalized.items():
        covered_by = None
        for other_name, other in normalized.items():
            if other_name == name:
                continue
            if path == other:
                # The same tree under two names: keep whichever came
                # first, so the choice does not depend on dict order.
                if other_name in kept:
                    covered_by = other_name
                    break
                continue
            if path.startswith(other.rstrip(os.sep) + os.sep):
                covered_by = other_name
                break
        if covered_by is not None:
            logger.debug(f'Not measuring local root {name} at {path} '
                         f'separately: already covered by {covered_by}')
            continue
        kept[name] = path
    return kept


def budget() -> Optional[Budget]:
    """Returns this container's declared ephemeral-storage allowance.

    Prefers the limit, which is the boundary that gets enforced, and falls
    back to the request, which is only what the deployment declared it
    needs. Returns None when neither is exposed -- there is no way to read
    the field from the kernel, since ephemeral-storage is not a cgroup
    controller.
    """
    for env_var, source in ((EPHEMERAL_STORAGE_LIMIT_ENV_VAR,
                             BUDGET_SOURCE_LIMIT),
                            (EPHEMERAL_STORAGE_REQUEST_ENV_VAR,
                             BUDGET_SOURCE_REQUEST)):
        raw = os.environ.get(env_var)
        if not raw:
            continue
        try:
            value = int(float(raw))
        except ValueError:
            logger.warning(f'Ignoring unparseable {env_var}={raw!r}')
            continue
        if value > 0:
            return Budget(total_bytes=value, source=source)
    return None


def _count_unreadable(error: OSError) -> int:
    """Returns 1 if *error* hides bytes from the walk, 0 if it does not."""
    return 0 if error.errno == errno.ENOENT else 1


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
    unreadable = 0
    truncated = False
    # Only inodes with more than one link can be reached twice, so only
    # those need to be remembered.
    seen: set = set()
    try:
        used += _entry_bytes(os.stat(path))
    except OSError as e:
        return RootUsage(path=path,
                         used_bytes=0,
                         files=0,
                         truncated=False,
                         unreadable=_count_unreadable(e))

    stack: List[str] = [path]
    while stack:
        if time.monotonic() >= deadline:
            # A tree of empty directories never reaches the per-entry check
            # below, so the deadline is enforced here too.
            truncated = True
            break
        current = stack.pop()
        try:
            scandir = os.scandir(current)
        except OSError as e:
            # A whole subtree missing from the total is worth reporting;
            # a directory that was simply GC'd away is not.
            unreadable += _count_unreadable(e)
            logger.debug(f'Skipping {current} while measuring {path}: {e}')
            continue
        with scandir:
            for entry in scandir:
                entries += 1
                if entries >= max_entries:
                    truncated = True
                    break
                if time.monotonic() >= deadline:
                    truncated = True
                    break
                try:
                    stat_result = entry.stat(follow_symlinks=False)
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError as e:
                    # ENOENT is the normal case here -- the request-log GC
                    # unlinks constantly, and those bytes really are gone.
                    # Anything else is a blind spot in the total.
                    unreadable += _count_unreadable(e)
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
    if unreadable:
        logger.info(f'Local disk scan of {path} could not read '
                    f'{unreadable} entries; reported size is a lower bound')
    return RootUsage(path=path,
                     used_bytes=used,
                     files=files,
                     truncated=truncated,
                     unreadable=unreadable)


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


def _mount_sources() -> Dict[str, str]:
    """Returns {mount point: source path inside its device}, or {} if unknown.

    The source path is mountinfo's fourth field, which for a bind mount is
    the subtree of the backing device that was mounted -- the only place a
    container can see what kind of volume it was handed.
    """
    sources: Dict[str, str] = {}
    try:
        with open('/proc/self/mountinfo', 'r', encoding='utf-8') as f:
            for line in f:
                fields = line.split()
                if len(fields) < 5:
                    continue
                # id parent major:minor source mount-point ...; both paths
                # octal-escape the characters that would break the split.
                sources[_unescape_mount_path(fields[4])] = _unescape_mount_path(
                    fields[3])
    except OSError as e:
        logger.debug(f'Could not read /proc/self/mountinfo: {e}')
        return {}
    return sources


def _unescape_mount_path(path: str) -> str:
    for escaped, literal in ((r'\040', ' '), (r'\011', '\t'), (r'\012', '\n'),
                             (r'\134', '\\')):
        path = path.replace(escaped, literal)
    return path


def _charged_to_ephemeral(mountpoint: str, sources: Dict[str, str],
                          container_root_device: Optional[int]) -> bool:
    """Whether writes under *mountpoint* count against the ephemeral budget.

    A positive test, matching what a container runtime charges to a
    container's ephemeral storage: its writable layer, and local
    scratch volumes. Anything else -- a persistent volume, a
    memory-backed tmpfs -- is not charged, and counting it would subtract
    another volume's bytes from this container's budget.

    Deliberately conservative in the direction that avoids false
    exhaustion: an unrecognised mount is treated as not charged.
    """
    try:
        if os.stat(mountpoint).st_dev == container_root_device:
            # The container's own writable layer.
            return True
    except OSError:
        return False
    source = sources.get(mountpoint)
    return source is not None and _EPHEMERAL_VOLUME_MARKER in source


def _filesystem_usage(paths: List[str]) -> Dict[str, FilesystemUsage]:
    """Returns space stats for the distinct filesystems holding *paths*."""
    out: Dict[str, FilesystemUsage] = {}
    sources = _mount_sources()
    try:
        container_root_device: Optional[int] = os.stat('/').st_dev
    except OSError:
        container_root_device = None
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
            avail_bytes=stats.f_bavail * stats.f_frsize,
            charged_to_ephemeral=_charged_to_ephemeral(mountpoint, sources,
                                                       container_root_device))
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
        usage = _scan_root(path, deadline, max_entries)
        usage.mountpoint = _mountpoint(path)
        roots[name] = usage
    return Snapshot(roots=roots,
                    filesystems=_filesystem_usage(
                        [path for _, path in measured]),
                    budget=budget(),
                    duration_seconds=time.monotonic() - started)
