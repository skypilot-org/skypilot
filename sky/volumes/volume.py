"""Volume types and access modes."""
import re
from typing import Any, Dict, Optional

from sky import sky_logging
from sky.utils import common_utils
from sky.utils import infra_utils
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import volume

logger = sky_logging.init_logger(__name__)

K8S_LABEL_PREFIX_MAX_LENGTH = 253
K8S_LABEL_NAME_MAX_LENGTH = 63
K8S_LABEL_VALUE_MAX_LENGTH = 63
K8S_LABEL_KEY_REGEX = r'^(([a-z0-9][-a-z0-9_.]*)?[a-z0-9]/)?([A-Za-z0-9][-A-Za-z0-9_.]*[A-Za-z0-9])$'  # pylint: disable=line-too-long
K8S_LABEL_VALUE_REGEX = r'^([A-Za-z0-9][-A-Za-z0-9_.]*[A-Za-z0-9])?$'


class Volume:
    """Volume specification."""

    def __init__(
            self,
            name: Optional[str] = None,
            type: Optional[str] = None,  # pylint: disable=redefined-builtin
            infra: Optional[str] = None,
            size: Optional[str] = None,
            labels: Optional[Dict[str, Any]] = None,
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

    def is_valid_label_key(self, key: str) -> bool:
        if self.type == volume.VolumeType.PVC.value:
            if not re.fullmatch(K8S_LABEL_KEY_REGEX, key):
                return False
            parts = str.split(key, '/')
            if len(parts) > 2:
                return False
            if len(parts) == 2:
                prefix = parts[0]
                name = parts[1]
                if not prefix:
                    return False
                if len(prefix) > K8S_LABEL_PREFIX_MAX_LENGTH:
                    return False
            if len(parts) == 1:
                name = parts[0]
            if not name:
                return False
            if len(name) > K8S_LABEL_NAME_MAX_LENGTH:
                return False
            return True
        else:
            return False

    def is_valid_label_value(self, value: str) -> bool:
        if self.type == volume.VolumeType.PVC.value:
            if not re.fullmatch(K8S_LABEL_VALUE_REGEX, value):
                return False
            if len(value) > K8S_LABEL_VALUE_MAX_LENGTH:
                return False
            return True
        else:
            return False

    def _validate_config(self) -> None:
        """Validate the volume config."""
        if not self.resource_name and not self.size:
            raise ValueError('Size is required for new volumes. '
                             'Please specify the size in the YAML file or '
                             'use the --size flag.')
        if self.labels:
            for key, value in self.labels.items():
                if not self.is_valid_label_key(key):
                    raise ValueError(f'Invalid label key: {key}')
                if not self.is_valid_label_value(value):
                    raise ValueError(f'Invalid label value: {value}')
