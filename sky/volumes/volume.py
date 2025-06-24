"""Volume types and access modes."""
import enum
import time
from typing import Any, Dict, Optional

from sky import exceptions
from sky import global_user_state
from sky import models
from sky.utils import common_utils
from sky.utils import infra_utils
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import status_lib


class VolumeType(enum.Enum):
    """Volume type."""
    PVC = 'k8s-pvc'


class VolumeAccessMode(enum.Enum):
    """Volume access mode."""
    READ_WRITE_ONCE = 'ReadWriteOnce'
    READ_WRITE_ONCE_POD = 'ReadWriteOncePod'
    READ_WRITE_MANY = 'ReadWriteMany'
    READ_ONLY_MANY = 'ReadOnlyMany'


class VolumeMount:
    """Volume mount specification."""

    def __init__(self, path: str, volume_name: str,
                 volume_config: models.VolumeConfig):
        self.path: str = path
        self.volume_name: str = volume_name
        self.volume_config: models.VolumeConfig = volume_config

    def pre_mount(self) -> None:
        """Update the volume status before actual mounting."""
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
        assert 'handle' in record, 'Volume handle is None.'
        volume_config: models.VolumeConfig = record['handle']
        return cls(path, volume_name, volume_config)

    @classmethod
    def from_yaml_config(cls, config: Dict[str, Any]) -> 'VolumeMount':
        common_utils.validate_schema(config, schemas.get_volume_mount_schema(),
                                     'Invalid volume mount config: ')

        path = config.pop('path', None)
        volume_name = config.pop('volume_name', None)
        volume_config: models.VolumeConfig = models.VolumeConfig.model_validate(
            config.pop('volume_config', None))
        return cls(path, volume_name, volume_config)

    def to_yaml_config(self) -> Dict[str, Any]:
        return {
            'path': self.path,
            'volume_name': self.volume_name,
            'volume_config': self.volume_config.model_dump(),
        }

    def __repr__(self):
        return (f'VolumeMount('
                f'\n\tpath={self.path},'
                f'\n\tvolume_name={self.volume_name},'
                f'\n\tvolume_config={self.volume_config})')


class Volume:
    """Volume specification."""

    def __init__(
            self,
            name: Optional[str] = None,
            type: Optional[str] = None,  # pylint: disable=redefined-builtin
            infra: Optional[str] = None,
            size: Optional[str] = None,
            resource_name: Optional[str] = None,
            config: Optional[Dict[str, Any]] = None):
        """Initialize a Volume instance.

        Args:
            name: Volume name
            type: Volume type (e.g., 'k8s-pvc')
            infra: Infrastructure specification
            size: Volume size
            config: Additional configuration
        """
        self.name = name
        self.type = type
        self.infra = infra
        self.size = size
        self.resource_name = resource_name
        self.config = config or {}

        self.cloud: Optional[str] = None
        self.region: Optional[str] = None
        self.zone: Optional[str] = None

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'Volume':
        """Create a Volume instance from a dictionary."""
        return cls(name=config_dict.get('name'),
                   type=config_dict.get('type'),
                   infra=config_dict.get('infra'),
                   size=config_dict.get('size'),
                   resource_name=config_dict.get('resource_name'),
                   config=config_dict.get('config', {}))

    def to_dict(self) -> Dict[str, Any]:
        """Convert the Volume to a dictionary."""
        return {
            'name': self.name,
            'type': self.type,
            'infra': self.infra,
            'size': self.size,
            'resource_name': self.resource_name,
            'config': self.config,
            'cloud': self.cloud,
            'region': self.region,
            'zone': self.zone,
        }

    def normalize_config(
            self,
            name: Optional[str] = None,
            infra: Optional[str] = None,
            type: Optional[str] = None,  # pylint: disable=redefined-builtin
            size: Optional[str] = None) -> None:
        """Override the volume config with CLI options,
           adjust and validate the config.

        Args:
            name: Volume name to override
            infra: Infrastructure to override
            type: Volume type to override
            size: Volume size to override
        """
        if name is not None:
            self.name = name
        if infra is not None:
            self.infra = infra
        if type is not None:
            self.type = type
        if size is not None:
            self.size = size

        # Validate schema
        common_utils.validate_schema(self.to_dict(),
                                     schemas.get_volume_schema(),
                                     'Invalid volumes config: ')

        # Adjust the volume config (e.g., parse size)
        self._adjust_config()

        # Validate the volume config
        self._validate_config()

        # Resolve the infrastructure options to cloud, region, zone
        infra_info = infra_utils.InfraInfo.from_str(self.infra)
        self.cloud = infra_info.cloud
        self.region = infra_info.region
        self.zone = infra_info.zone

    def _adjust_config(self) -> None:
        """Adjust the volume config (e.g., parse size)."""
        if self.size is None:
            return
        try:
            size = resources_utils.parse_memory_resource(self.size,
                                                         'size',
                                                         allow_rounding=True)
            if size == '0':
                raise ValueError('Size must be no less than 1Gi')
            self.size = size
        except ValueError as e:
            raise ValueError(f'Invalid size {self.size}: {e}') from e

    def _validate_config(self) -> None:
        """Validate the volume config."""
        if not self.resource_name and not self.size:
            raise ValueError('Size is required for new volumes. '
                             'Please specify the size in the YAML file or '
                             'use the --size flag.')
