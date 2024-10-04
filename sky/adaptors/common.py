"""Lazy import for modules to avoid import error when not used."""
import functools
import importlib
from typing import Any, Callable, Optional, Tuple


class LazyImport:
    """Lazy importer for heavy modules or cloud modules only when enabled.

    We use this for pandas and networkx, as they can be time-consuming to import
    (0.1-0.2 seconds). With this class, we can avoid the unnecessary import time
    when the module is not used (e.g., `networkx` should not be imported for
    `sky status and `pandas` should not be imported for `sky exec`).

    We also use this for cloud adaptors, because we do not want to import the
    cloud dependencies when it is not enabled.
    """

    def __init__(self,
                 module_name: str,
                 import_error_message: Optional[str] = None,
                 set_loggers: Optional[Callable] = None):
        self._module_name = module_name
        self._module = None
        self._import_error_message = import_error_message
        self._set_loggers = set_loggers

    def load_module(self):
        if self._module is None:
            try:
                self._module = importlib.import_module(self._module_name)
                if self._set_loggers is not None:
                    self._set_loggers()
            except ImportError as e:
                if self._import_error_message is not None:
                    raise ImportError(self._import_error_message) from e
                raise
        return self._module

    def __getattr__(self, name: str) -> Any:
        # Attempt to access the attribute, if it fails, assume it's a submodule
        # and lazily import it
        try:
            if name in self.__dict__:
                return self.__dict__[name]
            return getattr(self.load_module(), name)
        except AttributeError:
            # Dynamically create a new LazyImport instance for the submodule
            submodule_name = f'{self._module_name}.{name}'
            lazy_submodule = LazyImport(submodule_name,
                                        self._import_error_message)
            setattr(self, name, lazy_submodule)
            return lazy_submodule


def load_lazy_modules(modules: Tuple[LazyImport, ...]):
    """Load lazy modules before entering a function to error out quickly."""

    def decorator(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for m in modules:
                m.load_module()
            return func(*args, **kwargs)

        return wrapper

    return decorator
