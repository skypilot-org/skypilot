"""Utilities for nested config."""
import copy
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


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
    for key, value in override_config.items():
        (next_allowed_override_keys, next_disallowed_override_keys
        ) = _check_allowed_and_disallowed_override_keys(
            key, allowed_override_keys, disallowed_override_keys)
        if key == 'kubernetes' and key in base_config:
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
    logger.debug(f'User config: {".".join(keys)} -> {curr}')
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
            elif key in ['volumes', 'volumeMounts']:
                # If the key is 'volumes' or 'volumeMounts', we search for
                # item with the same name and merge it.
                for new_volume in value:
                    new_volume_name = new_volume.get('name')
                    if new_volume_name is not None:
                        destination_volume = next(
                            (v for v in base_config[key]
                             if v.get('name') == new_volume_name), None)
                        if destination_volume is not None:
                            merge_k8s_configs(destination_volume, new_volume)
                        else:
                            base_config[key].append(new_volume)
            else:
                base_config[key].extend(value)
        else:
            base_config[key] = value
