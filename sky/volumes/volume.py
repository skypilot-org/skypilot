"""Volume types and access modes."""
from typing import Any, Dict, Optional

from sky.utils import common_utils
from sky.utils import infra_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import schemas


class Volume:
    """Volume specification."""

    def __init__(
            self,
            name: Optional[str] = None,
            type: Optional[str] = None,  # pylint: disable=redefined-builtin
            infra: Optional[str] = None,
            size: Optional[str] = None,
            labels: Optional[Dict[str, str]] = None,
            resource_name: Optional[str] = None,
            config: Optional[Dict[str, Any]] = None):
        """Initialize a Volume instance.

        Args:
            name: Volume name
            type: Volume type (e.g., 'k8s-pvc')
            infra: Infrastructure specification
            size: Volume size
            labels: Volume labels
            config: Additional configuration
        """
        self.name = name
        self.type = type
        self.infra = infra
        self.size = size
        self.labels = labels or {}
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
                   labels=config_dict.get('labels'),
                   resource_name=config_dict.get('resource_name'),
                   config=config_dict.get('config', {}))

    def to_dict(self) -> Dict[str, Any]:
        """Convert the Volume to a dictionary."""
        return {
            'name': self.name,
            'type': self.type,
            'infra': self.infra,
            'size': self.size,
            'labels': self.labels,
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

        # Resolve the infrastructure options to cloud, region, zone
        infra_info = infra_utils.InfraInfo.from_str(self.infra)
        self.cloud = infra_info.cloud
        self.region = infra_info.region
        self.zone = infra_info.zone

        # Validate the volume config
        self._validate_config()

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
        assert self.cloud is not None, 'Cloud must be specified'
        cloud_obj = registry.CLOUD_REGISTRY.from_str(self.cloud)
        assert cloud_obj is not None

        valid, err_msg = cloud_obj.is_volume_name_valid(self.name)
        if not valid:
            raise ValueError(f'Invalid volume name: {err_msg}')

        if not self.resource_name and not self.size:
            raise ValueError('Size is required for new volumes. '
                             'Please specify the size in the YAML file or '
                             'use the --size flag.')
        if self.labels:
            for key, value in self.labels.items():
                valid, err_msg = cloud_obj.is_label_valid(key, value)
                if not valid:
                    raise ValueError(f'{err_msg}')
