"""Clouds need to be registered in CLOUD_REGISTRY to be discovered"""

import typing
from typing import Optional, Set, Type

from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.backends import backend
    from sky.clouds import cloud

T = typing.TypeVar('T')


class _Registry(dict, typing.Generic[T]):
    """Registry."""

    def __init__(self, registry_name: str, exclude: Optional[Set[str]]):
        super().__init__()
        self._registry_name = registry_name
        self._exclude = exclude or set()

    def from_str(self, name: Optional[str]) -> Optional[T]:
        if name is None:
            return None
        if name.lower() in self._exclude:
            return None
        if name.lower() not in self:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'{self._registry_name.capitalize()} {name!r} is not a '
                    f'valid {self._registry_name} among {list(self.keys())}')
        return self.get(name.lower())

    def register(self, cls: Type[T]) -> Type[T]:
        name = cls.__name__.lower()
        assert name not in self, f'{name} already registered'
        self[name] = cls()
        return cls


# Backward compatibility. global_user_state's DB may have recorded
# Local cloud, and we've just removed it from the registry, and
# global_user_state.get_enabled_clouds() would call into this func
# and fail.

CLOUD_REGISTRY: _Registry = _Registry['cloud.Cloud'](registry_name='cloud',
                                                     exclude={'local'})

BACKEND_REGISTRY: _Registry = _Registry['backend.Backend'](
    registry_name='backend', exclude=None)
