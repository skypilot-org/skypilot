"""Volume mount specification."""

import pickle
import time
from typing import Optional

from sky import exceptions
from sky import global_user_state
from sky import models
from sky.utils import status_lib


class VolumeMount:
    """Volume mount specification."""

    def __init__(self, path: str, volume_name: str):
        self.path: str = path
        self.volume_name: str = volume_name
        self.volume_config: Optional[models.VolumeConfig] = None
        self._resolved: bool = False

    def resolve(self) -> None:
        """Resolve the volume specification by name."""
        if self._resolved:
            return
        record = global_user_state.get_volume_by_name(self.volume_name)
        if record is None:
            raise exceptions.VolumeNotFoundError(
                f'Volume {self.volume_name} not found.')
        assert 'handle' in record, 'Volume handle is None.'
        volume_config: models.VolumeConfig = pickle.loads(record['handle'])
        self.volume_config = volume_config
        self._resolved = True

    def pre_mount(self) -> None:
        """Update the volume status before actual mounting."""
        assert self._resolved, 'Volume must be resolved before mounting.'
        # TODO(aylei): for ReadWriteOnce volume, we also need to queue the
        # mount request if the target volume is already mounted to another
        # cluster. For now, we only support ReadWriteMany volume.
        global_user_state.update_volume(self.volume_name,
                                        last_attached_at=int(time.time()),
                                        status=status_lib.VolumeStatus.IN_USE)
