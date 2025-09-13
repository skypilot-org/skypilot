"""Registry for classes to be discovered"""

import typing
from typing import Callable, Dict, List, Optional, Set, Type, Union

from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.backends import backend
    from sky.clouds import cloud
    from sky.jobs import recovery_strategy

T = typing.TypeVar('T')


class _Registry(dict, typing.Generic[T]):
    """Registry."""

    def __init__(self,
                 registry_name: str,
                 exclude: Optional[Set[str]],
                 type_register: bool = False):
        super().__init__()
        self._registry_name = registry_name
        self._exclude = exclude or set()
        self._default: Optional[str] = None
        self._type_register: bool = type_register
        self._aliases: Dict[str, str] = {}

    def from_str(self, name: Optional[str]) -> Optional[T]:
        """Returns the cloud instance from the canonical name or alias."""
        if name is None:
            return None

        search_name = name.lower()
        if search_name in self._exclude:
            return None

        if search_name in self:
            return self[search_name]

        if search_name in self._aliases:
            return self[self._aliases[search_name]]

        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'{self._registry_name.capitalize()} {name!r} is not a '
                f'valid {self._registry_name} among '
                f'{[*self.keys(), *self._aliases.keys()]}')

    def type_register(self,
                      name: str,
                      default: bool = False) -> Callable[[Type[T]], Type[T]]:

        name = name.lower()

        def decorator(cls: Type[T]) -> Type[T]:
            assert self._type_register, ('type_register can only be used '
                                         'when type_register is True')
            assert name not in self, f'{name} already registered'
            self[name] = cls
            if default:
                self._default = name
            return cls

        return decorator

    @typing.overload
    def register(self, cls: Type[T]) -> Type[T]:
        ...

    @typing.overload
    def register(
            self,
            cls: None = None,
            aliases: Optional[List[str]] = None
    ) -> Callable[[Type[T]], Type[T]]:
        ...

    def register(
        self,
        cls: Optional[Type[T]] = None,
        aliases: Optional[List[str]] = None
    ) -> Union[Type[T], Callable[[Type[T]], Type[T]]]:
        assert not self._type_register, ('register can only be used when '
                                         'type_register is False')

        def _register(cls: Type[T]) -> Type[T]:
            name = cls.__name__.lower()
            assert name not in self, f'{name} already registered'
            self[name] = cls()

            for alias in aliases or []:
                alias = alias.lower()
                assert alias not in self._aliases, f'{alias} already registered'
                self._aliases[alias] = name
            return cls

        if cls is not None:
            # Invocation without parentheses (e.g. @register)
            return _register(cls)

        # Invocation with parentheses (e.g. @register(aliases=['alias']))
        return _register

    @property
    def default(self) -> str:
        assert self._default is not None, ('default is not set', self)
        return self._default


# Backward compatibility. global_user_state's DB may have recorded
# Local cloud, and we've just removed it from the registry, and
# global_user_state.get_enabled_clouds() would call into this func
# and fail.

CLOUD_REGISTRY: _Registry = _Registry['cloud.Cloud'](registry_name='cloud',
                                                     exclude={'local'})

BACKEND_REGISTRY: _Registry = _Registry['backend.Backend'](
    registry_name='backend', type_register=True, exclude=None)

JOBS_RECOVERY_STRATEGY_REGISTRY: _Registry = (
    _Registry['recovery_strategy.StrategyExecutor'](
        registry_name='jobs recovery strategy',
        exclude=None,
        type_register=True))

# TODO(tian): Add a registry for spot placer.
