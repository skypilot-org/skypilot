"""Clouds need to be registered in CLOUD_REGISTRY to be discovered"""

import typing
from typing import Callable, Dict, List, Optional, Type

from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud


class _CloudRegistry:
    """Registry of clouds."""

    def __init__(self):
        self.clouds: Dict[str, 'cloud.Cloud'] = {}
        self.aliases: Dict[str, str] = {}

    def from_str(self, name: Optional[str]) -> Optional['cloud.Cloud']:
        if name is None:
            return None
        if name.lower() not in self.clouds:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Cloud {name!r} is not a valid cloud among '
                                 f'{list(self.clouds.keys())}')
        return self.clouds[name.lower()]

    def from_name_or_alias(self,
                           name: Optional[str]) -> Optional['cloud.Cloud']:
        if name is None:
            return None

        search_name = name.lower()

        if search_name in self.clouds:
            return self.clouds[search_name]

        if search_name in self.aliases:
            return self.clouds[self.aliases[search_name]]

        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Cloud {name!r} is not a valid cloud name among '
                             f'{[*self.clouds.keys(), *self.aliases.keys()]}')

    def register(
        self,
        aliases: Optional[List[str]] = None
    ) -> Callable[[Type['cloud.Cloud']], Type['cloud.Cloud']]:

        def _register(cloud_cls: Type['cloud.Cloud']) -> Type['cloud.Cloud']:
            name = cloud_cls.__name__.lower()
            assert name not in self.clouds, f'{name} already registered'
            self.clouds[name] = cloud_cls()

            for alias in aliases or []:
                assert alias not in self.aliases, (
                    f'alias {alias} already registered')
                self.aliases[alias] = name

            return cloud_cls

        return _register


CLOUD_REGISTRY: _CloudRegistry = _CloudRegistry()
