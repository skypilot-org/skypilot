"""Clouds need to be registered in CLOUD_REGISTRY to be discovered"""

import typing
from typing import Callable, Dict, List, Optional, overload, Type, Union

from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud


class _CloudRegistry(dict):
    """Registry of clouds."""

    def __init__(self) -> None:
        super().__init__()
        self.aliases: Dict[str, str] = {}

    def from_str(self, name: Optional[str]) -> Optional['cloud.Cloud']:
        """Returns the cloud instance from the canonical name or alias."""
        if name is None:
            return None

        search_name = name.lower()

        if search_name in self:
            return self[search_name]

        if search_name in self.aliases:
            return self[self.aliases[search_name]]

        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Cloud {name!r} is not a valid cloud among '
                             f'{[*self.keys(), *self.aliases.keys()]}')

    @overload
    def register(self, cloud_cls: Type['cloud.Cloud']) -> Type['cloud.Cloud']:
        ...

    @overload
    def register(
        self,
        cloud_cls: None = None,
        aliases: Optional[List[str]] = None,
    ) -> Callable[[Type['cloud.Cloud']], Type['cloud.Cloud']]:
        ...

    def register(
        self,
        cloud_cls: Optional[Type['cloud.Cloud']] = None,
        aliases: Optional[List[str]] = None,
    ) -> Union[Type['cloud.Cloud'], Callable[[Type['cloud.Cloud']],
                                             Type['cloud.Cloud']]]:

        def _register(cloud_cls: Type['cloud.Cloud']) -> Type['cloud.Cloud']:
            name = cloud_cls.canonical_name()
            assert name not in self, f'{name} already registered'
            self[name] = cloud_cls()

            for alias in aliases or []:
                alias = alias.lower()
                assert alias not in self.aliases, (
                    f'alias {alias} already registered')
                self.aliases[alias] = name

            return cloud_cls

        if cloud_cls is not None:
            # invocation without parens (e.g. just `@register`)
            return _register(cloud_cls)

        # Invocation with parens (e.g. `@register(aliases=['alias'])`)
        return _register


CLOUD_REGISTRY: _CloudRegistry = _CloudRegistry()
