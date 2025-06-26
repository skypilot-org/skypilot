"""Cloud config utils."""

from typing import Any, Dict, Optional, Tuple

from sky import skypilot_config
from sky.utils import config_utils


def get_cloud_config_value(
        cloud: str,
        keys: Tuple[str, ...],
        region: Optional[str] = None,
        default_value: Optional[Any] = None,
        override_configs: Optional[Dict[str, Any]] = None) -> Any:
    """Returns the nested key value by reading from config
    Order to get the property_name value:
    1. if region is specified,
       try to get the value from <cloud>/<region_key>/<region>/keys
    2. if no region or no override,
       try to get it at the cloud level <cloud>/keys
    3. if not found at cloud level,
       return either default_value if specified or None
    """
    property_value = None
    region_key = None
    if cloud == 'kubernetes':
        region_key = 'per_context_config'

    if region and region_key:
        property_value = skypilot_config.get_nested(
            keys=(cloud, region_key, region) + keys,
            default_value=None,
            override_configs=override_configs)
    # if no override found for specified region
    if property_value is None:
        property_value = skypilot_config.get_nested(
            keys=(cloud,) + keys,
            default_value=default_value,
            override_configs=override_configs)
    return property_value


def get_cloud_config_value_from_dict(
        dict_config: Dict[str, Any],
        cloud: str,
        keys: Tuple[str, ...],
        region: Optional[str] = None,
        default_value: Optional[Any] = None,
        override_configs: Optional[Dict[str, Any]] = None) -> Any:
    """Returns the nested key value by reading from config
    Order to get the property_name value:
    1. if region is specified,
       try to get the value from <cloud>/<region_key>/<region>/keys
    2. if no region or no override,
       try to get it at the cloud level <cloud>/keys
    3. if not found at cloud level,
       return either default_value if specified or None
    """
    input_config = config_utils.Config(dict_config)
    property_value = None
    region_key = None
    if cloud == 'kubernetes':
        region_key = 'per_context_config'

    if region and region_key:
        property_value = input_config.get_nested(
            keys=(cloud, region_key, region) + keys,
            default_value=None,
            override_configs=override_configs)
    # if no override found for specified region
    if property_value is None:
        property_value = input_config.get_nested(
            keys=(cloud,) + keys,
            default_value=default_value,
            override_configs=override_configs)
    return property_value
