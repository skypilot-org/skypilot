"""Volume utilities."""
import enum
import time
from typing import Any, Dict

from sky import exceptions
from sky import global_user_state
from sky import models
from sky.utils import common_utils
from sky.utils import schemas
from sky.utils import status_lib


class VolumeAccessMode(enum.Enum):
    """Volume access mode."""
    READ_WRITE_ONCE = 'ReadWriteOnce'
    READ_WRITE_ONCE_POD = 'ReadWriteOncePod'
    READ_WRITE_MANY = 'ReadWriteMany'
    READ_ONLY_MANY = 'ReadOnlyMany'


class VolumeType(enum.Enum):
    """Volume type."""
    PVC = 'k8s-pvc'


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
