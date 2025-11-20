"""Utilities for nested config."""
import copy
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

_REGION_CONFIG_CLOUDS = ['nebius', 'oci']

# Kubernetes API use list to represent dictionary fields with patch strategy
# merge and each item is indexed by the patch merge key. The following map
# maps the field name to the patch merge key.
# pylint: disable=line-too-long
# Ref: https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.27/#podspec-v1-core
# NOTE: field containers and imagePullSecrets are not included deliberately for
# backward compatibility (we only support one container per pod now).
_PATCH_MERGE_KEYS = {
    'initContainers': 'name',
    'ephemeralContainers': 'name',
    'volumes': 'name',
    'volumeMounts': 'name',
    'resourceClaims': 'name',
    'env': 'name',
    'hostAliases': 'ip',
    'topologySpreadConstraints': 'topologyKey',
    'ports': 'containerPort',
    'volumeDevices': 'devicePath',
}


class Config(Dict[str, Any]):
    """SkyPilot config that supports setting/getting values with nested keys."""

    def get_nested(
        self,
        keys: Tuple[str, ...],
        default_value: Any,
        override_configs: Optional[Dict[str, Any]] = None,
        allowed_override_keys: Optional[List[Tuple[str, ...]]] = None,
        disallowed_override_keys: Optional[List[Tuple[str,
                                                      ...]]] = None) -> Any:
        """Gets a nested key.

        If any key is not found, or any intermediate key does not point to a
        dict value, returns 'default_value'.

        Args:
            keys: A tuple of strings representing the nested keys.
            default_value: The default value to return if the key is not found.
            override_configs: A dict of override configs with the same schema as
                the config file, but only containing the keys to override.
            allowed_override_keys: A list of keys that are allowed to be
                overridden.
            disallowed_override_keys: A list of keys that are disallowed to be
                overridden.

        Returns:
            The value of the nested key, or 'default_value' if not found.
        """
        config = copy.deepcopy(self)
        if override_configs is not None:
            config = _recursive_update(config, override_configs,
                                       allowed_override_keys,
                                       disallowed_override_keys)
        return _get_nested(config, keys, default_value, pop=False)

    def set_nested(self, keys: Tuple[str, ...], value: Any) -> None:
        """In-place sets a nested key to value.

        Like get_nested(), if any key is not found, this will not raise an
        error.
        """
        override = {}
        for i, key in enumerate(reversed(keys)):
            if i == 0:
                override = {key: value}
            else:
                override = {key: override}
        _recursive_update(self, override)

    def pop_nested(self, keys: Tuple[str, ...], default_value: Any) -> Any:
        """Pops a nested key."""
        return _get_nested(self, keys, default_value, pop=True)

    @classmethod
    def from_dict(cls, config: Optional[Dict[str, Any]]) -> 'Config':
        if config is None:
            return cls()
        return cls(**config)


def _check_allowed_and_disallowed_override_keys(
    key: str,
    allowed_override_keys: Optional[List[Tuple[str, ...]]] = None,
    disallowed_override_keys: Optional[List[Tuple[str, ...]]] = None
) -> Tuple[Optional[List[Tuple[str, ...]]], Optional[List[Tuple[str, ...]]]]:
    allowed_keys_with_matched_prefix: Optional[List[Tuple[str, ...]]] = []
    disallowed_keys_with_matched_prefix: Optional[List[Tuple[str, ...]]] = []
    if allowed_override_keys is not None:
        for nested_key in allowed_override_keys:
            if key == nested_key[0]:
                if len(nested_key) == 1:
                    # Allowed key is fully matched, no need to check further.
                    allowed_keys_with_matched_prefix = None
                    break
                assert allowed_keys_with_matched_prefix is not None
                allowed_keys_with_matched_prefix.append(nested_key[1:])
        if (allowed_keys_with_matched_prefix is not None and
                not allowed_keys_with_matched_prefix):
            raise ValueError(f'Key {key} is not in allowed override keys: '
                             f'{allowed_override_keys}')
    else:
        allowed_keys_with_matched_prefix = None

    if disallowed_override_keys is not None:
        for nested_key in disallowed_override_keys:
            if key == nested_key[0]:
                if len(nested_key) == 1:
                    raise ValueError(
                        f'Key {key} is in disallowed override keys: '
                        f'{disallowed_override_keys}')
                assert disallowed_keys_with_matched_prefix is not None
                disallowed_keys_with_matched_prefix.append(nested_key[1:])
    else:
        disallowed_keys_with_matched_prefix = None
    return allowed_keys_with_matched_prefix, disallowed_keys_with_matched_prefix


def _recursive_update(
        base_config: Config,
        override_config: Dict[str, Any],
        allowed_override_keys: Optional[List[Tuple[str, ...]]] = None,
        disallowed_override_keys: Optional[List[Tuple[str,
                                                      ...]]] = None) -> Config:
    """Recursively updates base configuration with override configuration"""

    def _update_k8s_config(
        base_config: Config,
        override_config: Dict[str, Any],
        allowed_override_keys: Optional[List[Tuple[str, ...]]] = None,
        disallowed_override_keys: Optional[List[Tuple[str,
                                                      ...]]] = None) -> Config:
        """Updates the top-level k8s config with the override config."""
        for key, value in override_config.items():
            (next_allowed_override_keys, next_disallowed_override_keys
            ) = _check_allowed_and_disallowed_override_keys(
                key, allowed_override_keys, disallowed_override_keys)
            if key in ['custom_metadata', 'pod_config'] and key in base_config:
                merge_k8s_configs(base_config[key], value,
                                  next_allowed_override_keys,
                                  next_disallowed_override_keys)
            elif (isinstance(value, dict) and key in base_config and
                  isinstance(base_config[key], dict)):
                _recursive_update(base_config[key], value,
                                  next_allowed_override_keys,
                                  next_disallowed_override_keys)
            else:
                base_config[key] = value
        return base_config

    for key, value in override_config.items():
        (next_allowed_override_keys, next_disallowed_override_keys
        ) = _check_allowed_and_disallowed_override_keys(
            key, allowed_override_keys, disallowed_override_keys)
        if key == 'kubernetes' and key in base_config:
            _update_k8s_config(base_config[key], value,
                               next_allowed_override_keys,
                               next_disallowed_override_keys)
        elif (isinstance(value, dict) and key in base_config and
              isinstance(base_config[key], dict)):
            _recursive_update(base_config[key], value,
                              next_allowed_override_keys,
                              next_disallowed_override_keys)
        else:
            base_config[key] = value
    return base_config


def _get_nested(configs: Optional[Dict[str, Any]],
                keys: Tuple[str, ...],
                default_value: Any,
                pop: bool = False) -> Any:
    if configs is None:
        return default_value
    curr = configs
    for i, key in enumerate(keys):
        if isinstance(curr, dict) and key in curr:
            value = curr[key]
            if i == len(keys) - 1:
                if pop:
                    curr.pop(key, default_value)
            curr = value
        else:
            return default_value
    return curr


def merge_k8s_configs(
        base_config: Dict[Any, Any],
        override_config: Dict[Any, Any],
        allowed_override_keys: Optional[List[Tuple[str, ...]]] = None,
        disallowed_override_keys: Optional[List[Tuple[str,
                                                      ...]]] = None) -> None:
    """Merge two configs into the base_config.

    Updates nested dictionaries instead of replacing them.
    If a list is encountered, it will be appended to the base_config list.

    An exception is when the key is 'containers', in which case the
    first container in the list will be fetched and merge_dict will be
    called on it with the first container in the base_config list.
    """
    for key, value in override_config.items():
        (next_allowed_override_keys, next_disallowed_override_keys
        ) = _check_allowed_and_disallowed_override_keys(
            key, allowed_override_keys, disallowed_override_keys)
        if isinstance(value, dict) and key in base_config:
            merge_k8s_configs(base_config[key], value,
                              next_allowed_override_keys,
                              next_disallowed_override_keys)
        elif isinstance(value, list) and key in base_config:
            assert isinstance(base_config[key], list), \
                f'Expected {key} to be a list, found {base_config[key]}'
            if key in ['containers', 'imagePullSecrets']:
                # If the key is 'containers' or 'imagePullSecrets, we take the
                # first and only container/secret in the list and merge it, as
                # we only support one container per pod.
                assert len(value) == 1, \
                    f'Expected only one container, found {value}'
                merge_k8s_configs(base_config[key][0], value[0],
                                  next_allowed_override_keys,
                                  next_disallowed_override_keys)
            # For list fields with patch strategy "merge", we merge the list
            # by the patch merge key.
            elif key in _PATCH_MERGE_KEYS:
                patch_merge_key = _PATCH_MERGE_KEYS[key]
                for override_item in value:
                    override_item_name = override_item.get(patch_merge_key)
                    if override_item_name is not None:
                        existing_base_item = next(
                            (v for v in base_config[key]
                             if v.get(patch_merge_key) == override_item_name),
                            None)
                        if existing_base_item is not None:
                            merge_k8s_configs(existing_base_item, override_item)
                        else:
                            base_config[key].append(override_item)
                    else:
                        base_config[key].append(override_item)
            else:
                base_config[key].extend(value)
        else:
            base_config[key] = value


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
    input_config = Config(dict_config)
    region_key = None
    if cloud in ('kubernetes', 'ssh'):
        region_key = 'context_configs'
    elif cloud in _REGION_CONFIG_CLOUDS:
        region_key = 'region_configs'

    per_context_config = None
    if region is not None and region_key is not None:
        per_context_config = input_config.get_nested(
            keys=(cloud, region_key, region) + keys,
            default_value=None,
            override_configs=override_configs)
        if not per_context_config and cloud in _REGION_CONFIG_CLOUDS:
            # TODO (kyuds): Backward compatibility, remove after 0.11.0.
            per_context_config = input_config.get_nested(
                keys=(cloud, region) + keys,
                default_value=None,
                override_configs=override_configs)
            if per_context_config is not None:
                logger.info(
                    f'{cloud} configuration is using the legacy format. \n'
                    'This format will be deprecated after 0.11.0, refer to '
                    '`https://docs.skypilot.co/en/latest/reference/config.html` '  # pylint: disable=line-too-long
                    'for the new format. Please use `region_configs` to specify region specific configuration.'
                )
    # if no override found for specified region
    general_config = input_config.get_nested(keys=(cloud,) + keys,
                                             default_value=default_value,
                                             override_configs=override_configs)

    if (cloud == 'kubernetes' and isinstance(general_config, dict) and
            isinstance(per_context_config, dict)):
        merge_k8s_configs(general_config, per_context_config)
        return general_config
    else:
        return (general_config
                if per_context_config is None else per_context_config)
