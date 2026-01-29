"""Volume utilities."""
from dataclasses import dataclass
import enum
import time
from typing import Any, Dict, Optional

from sky import exceptions
from sky import global_user_state
from sky import models
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


class VolumeMount:
    """Volume mount specification."""

    def __init__(self,
                 path: str,
                 volume_name: str,
                 volume_config: models.VolumeConfig,
                 is_ephemeral: bool = False):
        self.path: str = path
        self.volume_name: str = volume_name
        self.volume_config: models.VolumeConfig = volume_config
        self.is_ephemeral: bool = is_ephemeral

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
    def resolve(cls, path: str, volume_name: str) -> 'VolumeMount':
        """Resolve the volume mount by populating metadata of volume."""
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
        return cls(path, volume_name, volume_config)

    @classmethod
    def from_yaml_config(cls, config: Dict[str, Any]) -> 'VolumeMount':
        common_utils.validate_schema(config, schemas.get_volume_mount_schema(),
                                     'Invalid volume mount config: ')

        path = config.pop('path', None)
        volume_name = config.pop('volume_name', None)
        is_ephemeral = config.pop('is_ephemeral', False)
        volume_config: models.VolumeConfig = models.VolumeConfig.model_validate(
            config.pop('volume_config', None))
        return cls(path, volume_name, volume_config, is_ephemeral)

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
        return {
            'path': self.path,
            'volume_name': self.volume_name,
            'volume_config': self.volume_config.model_dump(),
            'is_ephemeral': self.is_ephemeral,
        }

    @property
    def name(self) -> str:
        """Return the volume name for use in provisioning."""
        return self.volume_name

    def __repr__(self):
        return (f'VolumeMount('
                f'\n\tpath={self.path},'
                f'\n\tvolume_name={self.volume_name},'
                f'\n\tis_ephemeral={self.is_ephemeral},'
                f'\n\tvolume_config={self.volume_config})')
