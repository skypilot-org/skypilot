"""Clouds need to be registered in CLOUD_REGISTRY to be discovered"""

import typing
from typing import Optional, Type

from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud


class _CloudRegistry(dict):
    """Registry of clouds."""

    def from_str(self, name: Optional[str]) -> Optional['cloud.Cloud']:
        if name is None:
            return None
        if name.lower() == 'local':
            # Backward compatibility. global_user_state's DB may have recorded
            # Local cloud, and we've just removed it from the registry, and
            # global_user_state.get_enabled_clouds() would call into this func
            # and fail.
            #
            # TODO(skypilot): have a better way to handle clouds removed from
            # registry if needed.
            return None
        if name.lower() not in self:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Cloud {name!r} is not a valid cloud among '
                                 f'{list(self.keys())}')
        return self.get(name.lower())

    def register(self, cloud_cls: Type['cloud.Cloud']) -> Type['cloud.Cloud']:
        name = cloud_cls.__name__.lower()
        assert name not in self, f'{name} already registered'
        self[name] = cloud_cls()
        return cloud_cls


CLOUD_REGISTRY: _CloudRegistry = _CloudRegistry()
