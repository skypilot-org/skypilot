import dataclasses
from typing import Optional, Dict, Any


@dataclasses.dataclass
class MountCachedConfig:
    """Per-bucket configuration for MOUNT_CACHED mode (rclone flags).

    Each field maps to a specific rclone flag. None means "use the default
    from get_mount_cached_cmd" (i.e., the flag is not overridden).
    """
    # Number of file transfers to run in parallel.
    # rclone flag: --transfers
    transfers: Optional[int] = None
    # Number of threads for multi-part upload of a single file.
    # rclone flag: --multi-thread-streams
    multi_thread_streams: Optional[int] = None
    # In-memory buffer size per transfer (e.g. "64M").
    # rclone flag: --buffer-size
    buffer_size: Optional[str] = None
    # Maximum total size of the VFS cache on disk (e.g. "20G").
    # rclone flag: --vfs-cache-max-size
    _vfs_cache_max_size: Optional[str] = None
    # Maximum age of objects in the VFS cache (e.g. "1h").
    # rclone flag: --vfs-cache-max-age
    vfs_cache_max_age: Optional[str] = None
    # Read-ahead bytes beyond what was requested (e.g. "128M").
    # rclone flag: --vfs-read-ahead
    vfs_read_ahead: Optional[str] = None
    # Initial chunk size for each read (e.g. "32M").
    # rclone flag: --vfs-read-chunk-size
    vfs_read_chunk_size: Optional[str] = None
    # Number of parallel streams for chunked reading.
    # When set, disables the default exponential chunk-size growth.
    # rclone flag: --vfs-read-chunk-streams
    vfs_read_chunk_streams: Optional[int] = None
    # Use recursive list operations. Good for many small files.
    # rclone flag: --fast-list
    fast_list: Optional[bool] = None
    # Delay before writing back to remote (e.g. "5s").
    # rclone flag: --vfs-write-back
    _vfs_write_back: Optional[str] = None
    # Mount as read-only.
    # rclone flag: --read-only
    read_only: Optional[bool] = None

    @property
    def vfs_cache_max_size(self) -> str:
        if self._vfs_cache_max_size is None:
            return '10G'  # SkyPilot default
        return self._vfs_cache_max_size

    @property
    def vfs_write_back(self) -> int:
        if self._vfs_write_back is None:
            return '1s'  # SkyPilot default
        return self._vfs_write_back

    def to_rclone_flags(self) -> str:
        """Convert non-None fields to rclone CLI flag string."""
        flags = []
        if self.transfers is not None:
            flags.append(f'--transfers {self.transfers}')
            # Automate checkers: transfers * 2
            flags.append(f'--checkers {self.transfers * 2}')
        if self.multi_thread_streams is not None:
            flags.append(
                f'--multi-thread-streams {self.multi_thread_streams}')
        if self.buffer_size is not None:
            flags.append(f'--buffer-size {self.buffer_size}')
        flags.append(f'--vfs-cache-max-size {self.vfs_cache_max_size}')
        if self.vfs_cache_max_age is not None:
            flags.append(f'--vfs-cache-max-age {self.vfs_cache_max_age}')
        if self.vfs_read_ahead is not None:
            flags.append(f'--vfs-read-ahead {self.vfs_read_ahead}')
        if self.vfs_read_chunk_size is not None:
            flags.append(f'--vfs-read-chunk-size {self.vfs_read_chunk_size}')
        if self.vfs_read_chunk_streams is not None:
            flags.append(
                f'--vfs-read-chunk-streams {self.vfs_read_chunk_streams}')
        if self.fast_list:
            flags.append('--fast-list')
        flags.append(f'--vfs-write-back {self.vfs_write_back}')
        if self.read_only:
            flags.append('--read-only')
        return ' '.join(flags)

    def to_yaml_config(self) -> Dict[str, Any]:
        """Serialize non-None fields to a dict for YAML round-tripping."""
        result = {}
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if value is not None:
                result[field.name] = value
        return result

    @classmethod
    def from_yaml_config(cls, config: Dict[str, Any]) -> 'MountCachedConfig':
        """Create from a dict parsed from YAML."""
        return cls(**config)
    
    def override(self, other: 'MountCachedConfig') -> None:
        """Override fields in self with non-None fields from other."""
        if other.transfers is not None:
            self.transfers = other.transfers
        if other.multi_thread_streams is not None:
            self.multi_thread_streams = other.multi_thread_streams
        if other.buffer_size is not None:
            self.buffer_size = other.buffer_size
        if other._vfs_cache_max_size is not None:
            self._vfs_cache_max_size = other._vfs_cache_max_size
        if other.vfs_cache_max_age is not None:
            self.vfs_cache_max_age = other.vfs_cache_max_age
        if other.vfs_read_ahead is not None:
            self.vfs_read_ahead = other.vfs_read_ahead
        if other.vfs_read_chunk_size is not None:
            self.vfs_read_chunk_size = other.vfs_read_chunk_size
        if other.vfs_read_chunk_streams is not None:
            self.vfs_read_chunk_streams = other.vfs_read_chunk_streams
        if other.fast_list is not None:
            self.fast_list = other.fast_list
        if other._vfs_write_back is not None:
            self._vfs_write_back = other._vfs_write_back
        if other.read_only is not None:
            self.read_only = other.read_only
