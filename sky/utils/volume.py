"""Volume utilities."""
from dataclasses import dataclass
import enum
import re
import time
from typing import Any, Dict, Optional, Tuple

from sky import exceptions
from sky import global_user_state
from sky import models
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import status_lib

MIN_RUNPOD_NETWORK_VOLUME_SIZE_GB = 10


class VolumeAccessMode(enum.Enum):
    """Volume access mode."""
    READ_WRITE_ONCE = 'ReadWriteOnce'
    READ_WRITE_ONCE_POD = 'ReadWriteOncePod'
    READ_WRITE_MANY = 'ReadWriteMany'
    READ_ONLY_MANY = 'ReadOnlyMany'


class VolumeType(enum.Enum):
    """Volume type."""
    PVC = 'k8s-pvc'
    HOSTPATH = 'k8s-hostpath'
    RUNPOD_NETWORK_VOLUME = 'runpod-network-volume'

    @classmethod
    def supported_types(cls) -> list:
        """Return list of supported volume type values."""
        return [vt.value for vt in cls]


EPHEMERAL_VOLUME_TYPES = [VolumeType.PVC.value]


@dataclass
class VolumeInfo:
    """Represents volume info."""
    name: str
    path: str
    volume_name_on_cloud: Optional[str] = None
    volume_id_on_cloud: Optional[str] = None
    sub_path: Optional[str] = None
    volume_type: Optional[str] = None
    host_path: Optional[str] = None


class VolumeMount:
    """Volume mount specification."""

    def __init__(self,
                 path: str,
                 volume_name: str,
                 volume_config: models.VolumeConfig,
                 is_ephemeral: bool = False,
                 sub_path: Optional[str] = None):
        self.path: str = path
        self.volume_name: str = volume_name
        self.volume_config: models.VolumeConfig = volume_config
        self.is_ephemeral: bool = is_ephemeral
        self.sub_path: Optional[str] = sub_path

    def pre_mount(self) -> None:
        """Update the volume status before actual mounting."""
        # Skip pre_mount for ephemeral volumes as they don't exist yet
        if self.is_ephemeral:
            return
        # TODO(aylei): for ReadWriteOnce volume, we also need to queue the
        # mount request if the target volume is already mounted to another
        # cluster. For now, we only support ReadWriteMany volume.
        global_user_state.update_volume(self.volume_name,
                                        last_attached_at=int(time.time()),
                                        status=status_lib.VolumeStatus.IN_USE)

    @classmethod
    def resolve(cls,
                path: str,
                volume_name: str,
                sub_path: Optional[str] = None) -> 'VolumeMount':
        """Resolve the volume mount by populating metadata of volume."""
        if sub_path is not None:
            if not re.match(constants.SUB_PATH_PATTERN, sub_path):
                raise ValueError(
                    f'sub_path contains invalid characters: {sub_path!r}. '
                    'Must be a relative path containing only '
                    'alphanumeric characters, dots, slashes, '
                    'underscores and hyphens, and must not start '
                    'with a slash.')
            if '..' in sub_path.split('/'):
                raise ValueError(
                    f'sub_path must not contain directory traversal '
                    f'(..): {sub_path!r}')
        record = global_user_state.get_volume_by_name(volume_name)
        if record is None:
            raise exceptions.VolumeNotFoundError(
                f'Volume {volume_name} not found.')
        if record.get('status') == status_lib.VolumeStatus.NOT_READY:
            error_message = record.get('error_message')
            msg = f'Volume {volume_name} is not ready.'
            if error_message:
                msg += f' Error: {error_message}'
            raise exceptions.VolumeNotReadyError(msg)
        assert 'handle' in record, 'Volume handle is None.'
        volume_config: models.VolumeConfig = record['handle']
        return cls(path, volume_name, volume_config, sub_path=sub_path)

    @classmethod
    def from_yaml_config(cls, config: Dict[str, Any]) -> 'VolumeMount':
        common_utils.validate_schema(config, schemas.get_volume_mount_schema(),
                                     'Invalid volume mount config: ')

        path = config.pop('path', None)
        volume_name = config.pop('volume_name', None)
        is_ephemeral = config.pop('is_ephemeral', False)
        sub_path = config.pop('sub_path', None)
        volume_config: models.VolumeConfig = models.VolumeConfig.model_validate(
            config.pop('volume_config', None))
        return cls(path,
                   volume_name,
                   volume_config,
                   is_ephemeral,
                   sub_path=sub_path)

    @classmethod
    def resolve_ephemeral_config(cls, path: str,
                                 config: Dict[str, Any]) -> 'VolumeMount':
        """Create an ephemeral volume mount from inline config.

        Args:
            path: The mount path for the volume.
            config: The volume configuration dict with size, and
                optional type, labels, and config fields, etc.

        Returns:
            A VolumeMount instance for the ephemeral volume.
        """
        volume_type = config.get('type')
        if volume_type and volume_type.lower() not in EPHEMERAL_VOLUME_TYPES:
            raise ValueError(f'Unsupported ephemeral volume type: '
                             f'{volume_type}. Supported types: '
                             f'{", ".join(EPHEMERAL_VOLUME_TYPES)}')
        size_config = config.get('size')
        if size_config is None:
            raise ValueError('Volume size must be specified for ephemeral '
                             'volumes.')
        try:
            size = resources_utils.parse_memory_resource(size_config,
                                                         'size',
                                                         allow_rounding=True)
            if size == '0':
                raise ValueError('Size must be no less than 1Gi')
        except ValueError as e:
            raise ValueError(
                f'Invalid size {size_config} for ephemeral volume: {e}') from e

        # Create VolumeConfig for ephemeral volume
        # Note: the empty fields will be populated during provisioning
        volume_config = models.VolumeConfig(
            name='',
            type=config.get('type', ''),
            # Default to kubernetes cloud here for backward compatibility,
            # but this will be reset to the correct cloud during provisioning.
            cloud='kubernetes',
            region=None,
            zone=None,
            name_on_cloud='',
            size=size,
            config=config.get('config', {}),
            labels=config.get('labels'),
        )

        return cls(path, '', volume_config, is_ephemeral=True)

    def to_yaml_config(self) -> Dict[str, Any]:
        config = {
            'path': self.path,
            'volume_name': self.volume_name,
            'volume_config': self.volume_config.model_dump(),
            'is_ephemeral': self.is_ephemeral,
        }
        if self.sub_path is not None:
            config['sub_path'] = self.sub_path
        return config

    @property
    def name(self) -> str:
        """Return the volume name for use in provisioning."""
        return self.volume_name

    def __repr__(self):
        return (f'VolumeMount('
                f'\n\tpath={self.path},'
                f'\n\tvolume_name={self.volume_name},'
                f'\n\tis_ephemeral={self.is_ephemeral},'
                f'\n\tsub_path={self.sub_path},'
                f'\n\tvolume_config={self.volume_config})')


class VolumeMountConflictChecker:
    """Detects conflicts between volume mounts from different sources.

    Three checks are performed as each volume mount is registered:
    1. Mount path uniqueness — no two mounts can share the same path.
    2. Volume name consistency — if two mounts share a volume name,
       they must reference the same underlying volume (same PVC claim
       or host directory).  Otherwise the template's name-based dedup
       silently drops one volume definition.
    3. Same PVC from different volume entries — different volume names
       pointing to the same PVC is almost certainly a misconfiguration.
       Refer to https://github.com/kubernetes/kubernetes/issues/127004.
    """

    def __init__(self):
        # mount_path -> (source, volume_desc)
        self._seen_mount_paths: Dict[str, Tuple[str, str]] = {}
        # volume_name -> (source, volume_desc, vol_source_id)
        self._seen_volume_names: Dict[str, Tuple[str, str, Optional[str]]] = {}
        # volume_name_on_cloud -> (volume_name, source, volume_desc)
        self._seen_pvcs: Dict[str, Tuple[str, str, str]] = {}

    @staticmethod
    def _get_vol_source_identity(
            volume_type: Optional[str],
            vol_name_on_cloud: Optional[str] = None,
            vol_host_path: Optional[str] = None) -> Optional[str]:
        """Return a type-aware volume source identity string.

        Each volume type defines its own identity key. Returns None if
        the type is unknown or the identity cannot be determined (e.g.,
        ephemeral volumes before provisioning).
        """
        if volume_type == VolumeType.PVC.value and vol_name_on_cloud:
            return f'pvc:{vol_name_on_cloud}'
        if volume_type == VolumeType.HOSTPATH.value and vol_host_path:
            return f'hostpath:{vol_host_path}'
        return None

    def check(self, vol: VolumeInfo, source: str, volume_desc: str) -> None:
        """Check for volume mount conflicts and register the entry.

        Args:
            vol: Volume info to check.
            source: Where the volume came from (e.g. 'task YAML volumes',
                'auto_mounts config').
            volume_desc: Human-readable description for error messages.

        Raises ValueError on conflict with a message identifying both
        conflicting volumes and their sources.
        """
        # Check 1: Mount path uniqueness
        if vol.path in self._seen_mount_paths:
            prev_source, prev_desc = self._seen_mount_paths[vol.path]
            raise ValueError(
                f'Volume mount path conflict: {vol.path!r} is '
                f'mounted by {volume_desc} (from {source}) and also '
                f'by {prev_desc} (from {prev_source}). '
                f'Please remove the duplicate from your task YAML '
                f'volumes config or auto_mounts config.')
        self._seen_mount_paths[vol.path] = (source, volume_desc)

        if not vol.name:
            return

        # Check 2: Volume name consistency.
        # Volume definitions are deduplicated by name but volume mounts
        # are not. If two entries share the same name but reference
        # different volume sources, one volume definition is silently
        # dropped.
        vol_source_id = self._get_vol_source_identity(vol.volume_type,
                                                      vol.volume_name_on_cloud,
                                                      vol.host_path)
        if vol.name in self._seen_volume_names:
            prev_src, prev_desc, prev_vol_source_id = (
                self._seen_volume_names[vol.name])
            # If identity is None (unknown type or ephemeral), we
            # cannot confirm they are the same volume, so treat same
            # name as a conflict.
            if (vol_source_id is None or prev_vol_source_id is None or
                    vol_source_id != prev_vol_source_id):
                raise ValueError(
                    f'Volume name conflict: volume name '
                    f'{vol.name!r} is used by {volume_desc} '
                    f'(from {source}) and by {prev_desc} '
                    f'(from {prev_src}), but they reference different '
                    f'volumes. Please remove the duplicate from your task '
                    f'YAML volumes config or auto_mounts config.')
            # Same name, same volume source: OK (e.g. auto_mount with
            # multiple mount_paths).
            return
        self._seen_volume_names[vol.name] = (source, volume_desc, vol_source_id)

        # Check 3: Same PVC from different volume entries.
        if (vol.volume_type == VolumeType.PVC.value and
                vol.volume_name_on_cloud):
            if vol.volume_name_on_cloud in self._seen_pvcs:
                prev_vol_name, prev_src, prev_desc = (
                    self._seen_pvcs[vol.volume_name_on_cloud])
                if prev_vol_name != vol.name:
                    raise ValueError(
                        f'Volume PVC conflict: PVC '
                        f'{vol.volume_name_on_cloud!r} is referenced '
                        f'by {volume_desc} (from {source}) and also '
                        f'by {prev_desc} (from {prev_src}). If you '
                        f'need to mount different sub-paths of the '
                        f'same PVC, use a single volume entry with '
                        f'sub_path. Please remove the '
                        f'duplicate from your task YAML volumes '
                        f'config or auto_mounts config.')
            self._seen_pvcs[vol.volume_name_on_cloud] = (vol.name, source,
                                                         volume_desc)
