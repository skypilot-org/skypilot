"""Volume types and access modes."""
from typing import Any, Dict, Optional

from sky import clouds
from sky.utils import common_utils
from sky.utils import infra_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import volume as volume_lib

VOLUME_TYPE_TO_CLOUD = {
    volume_lib.VolumeType.PVC: clouds.Kubernetes(),
    volume_lib.VolumeType.RUNPOD_NETWORK_VOLUME: clouds.RunPod(),
}


class Volume:
    """Volume specification."""

    def __init__(
            self,
            name: Optional[str] = None,
            type: Optional[str] = None,  # pylint: disable=redefined-builtin
            infra: Optional[str] = None,
            size: Optional[str] = None,
            labels: Optional[Dict[str, str]] = None,
            use_existing: Optional[bool] = None,
            config: Optional[Dict[str, Any]] = None):
        """Initialize a Volume instance.

        Args:
            name: Volume name
            type: Volume type (e.g., 'k8s-pvc')
            infra: Infrastructure specification
            size: Volume size
            labels: Volume labels
            use_existing: Whether to use an existing volume
            config: Additional configuration
        """
        self.name = name
        self.type = type
        self.infra = infra
        self.size = size
        self.labels = labels or {}
        self.use_existing = use_existing
        self.config = config or {}

        self.cloud: Optional[str] = None
        self.region: Optional[str] = None
        self.zone: Optional[str] = None

        self._normalize_config()

    @classmethod
    def from_yaml_config(cls, config: Dict[str, Any]) -> 'Volume':
        """Create a Volume subclass instance from a dictionary via factory."""
        vol_type_val = config.get('type')
        try:
            vt = (volume_lib.VolumeType(vol_type_val)
                  if vol_type_val is not None else None)
        except Exception:  # pylint: disable=broad-except
            vt = None

        if vt is None:
            raise ValueError(f'Invalid volume type: {vol_type_val}')

        if vt == volume_lib.VolumeType.PVC:
            return PVCVolume(name=config.get('name'),
                             type=vol_type_val,
                             infra=config.get('infra'),
                             size=config.get('size'),
                             labels=config.get('labels'),
                             use_existing=config.get('use_existing'),
                             config=config.get('config', {}))
        if vt == volume_lib.VolumeType.RUNPOD_NETWORK_VOLUME:
            return RunpodNetworkVolume(name=config.get('name'),
                                       type=vol_type_val,
                                       infra=config.get('infra'),
                                       size=config.get('size'),
                                       labels=config.get('labels'),
                                       use_existing=config.get('use_existing'),
                                       config=config.get('config', {}))

        raise ValueError(f'Invalid volume type: {vol_type_val}')

    def to_yaml_config(self) -> Dict[str, Any]:
        """Convert the Volume to a dictionary."""
        return {
            'name': self.name,
            'type': self.type,
            'infra': self.infra,
            'size': self.size,
            'labels': self.labels,
            'use_existing': self.use_existing,
            'config': self.config,
            'cloud': self.cloud,
            'region': self.region,
            'zone': self.zone,
        }

    def _normalize_config(self) -> None:
        """Normalize and validate the config."""
        # Validate schema
        common_utils.validate_schema(self.to_yaml_config(),
                                     schemas.get_volume_schema(),
                                     'Invalid volumes config: ')

        # Adjust the volume config (e.g., parse size)
        self._adjust_config()

        # Resolve the infrastructure options to cloud, region, zone
        infra_info = infra_utils.InfraInfo.from_str(self.infra)
        self.cloud = infra_info.cloud
        self.region = infra_info.region
        self.zone = infra_info.zone

        # Set cloud from volume type if not specified
        cloud_obj_from_type = VOLUME_TYPE_TO_CLOUD.get(
            volume_lib.VolumeType(self.type))
        if self.cloud:
            cloud_obj = registry.CLOUD_REGISTRY.from_str(self.cloud)
            assert cloud_obj is not None
            if not cloud_obj.is_same_cloud(cloud_obj_from_type):
                raise ValueError(
                    f'Invalid cloud {self.cloud} for volume type {self.type}')
        else:
            self.cloud = str(cloud_obj_from_type)

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

    def validate(self, skip_cloud_compatibility: bool = False) -> None:
        """Validates the volume."""
        self.validate_name()
        self.validate_size()
        if not skip_cloud_compatibility:
            self.validate_cloud_compatibility()
        # Extra, type-specific validations
        self._validate_config_extra()

    def validate_name(self) -> None:
        """Validates if the volume name is set."""
        assert self.name is not None, 'Volume name must be set'

    def validate_size(self) -> None:
        """Validates that size is specified for new volumes."""
        if not self.use_existing and not self.size:
            raise ValueError('Size is required for new volumes. '
                             'Please specify the size in the YAML file or '
                             'use the --size flag.')

    def validate_cloud_compatibility(self) -> None:
        """Validates region, zone, name, labels with the cloud."""
        cloud_obj = registry.CLOUD_REGISTRY.from_str(self.cloud)
        assert cloud_obj is not None

        valid, err_msg = cloud_obj.is_volume_name_valid(self.name)
        if not valid:
            raise ValueError(f'Invalid volume name: {err_msg}')

        if self.labels:
            for key, value in self.labels.items():
                valid, err_msg = cloud_obj.is_label_valid(key, value)
                if not valid:
                    raise ValueError(f'{err_msg}')

    # Hook methods for subclasses
    def _validate_config_extra(self) -> None:
        """Additional type-specific validation.

        Subclasses can override to enforce stricter rules.
        """
        return


class PVCVolume(Volume):
    """Kubernetes PVC-backed volume."""
    pass


class RunpodNetworkVolume(Volume):
    """RunPod Network Volume."""

    def _validate_config_extra(self) -> None:
        if not self.use_existing and self.size is not None:
            try:
                size_int = int(self.size)
                if size_int < volume_lib.MIN_RUNPOD_NETWORK_VOLUME_SIZE_GB:
                    raise ValueError(
                        f'RunPod network volume size must be at least '
                        f'{volume_lib.MIN_RUNPOD_NETWORK_VOLUME_SIZE_GB}GB.')
            except Exception as e:  # pylint: disable=broad-except
                raise ValueError(f'Invalid volume size {self.size!r}: '
                                 f'{e}') from e
        if not self.zone:
            raise ValueError('RunPod DataCenterId is required for network '
                             'volumes. Set the zone in the infra field.')

        return
