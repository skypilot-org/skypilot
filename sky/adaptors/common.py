"""Lazy import for modules to avoid import error when not used."""
import functools
import importlib
import threading
import types
from typing import Any, Callable, Optional, Tuple


class LazyImport(types.ModuleType):
    """Lazy importer for modules.

    This is mainly used in two cases:
    1. Heavy 3rd party modules: They can be time-consuming to import
    and not necessary for all `sky` imports, e.g., numpy(700+ms),
    pendulum(500+ms), cryptography(500+ms), pandas(200+ms), and
    networkx(100+ms), etc. With this class, we can avoid the
    unnecessary import time when the module is not used (e.g.,
    `networkx` should not be imported for `sky status` and `pandas`
    should not be imported for `sky exec`).

    2. Cloud modules in cloud adaptors: cloud dependencies are only required
    when a cloud is enabled, so we only import them when actually needed.
    """

    def __init__(self,
                 module_name: str,
                 import_error_message: Optional[str] = None,
                 set_loggers: Optional[Callable] = None):
        self._module_name = module_name
        self._module = None
        self._import_error_message = import_error_message
        self._set_loggers = set_loggers
        self._lock = threading.RLock()

    def load_module(self):
        # Avoid extra imports when multiple threads try to import the same
        # module. The overhead is minor since import can only run in serial
        # due to GIL even in multi-threaded environments.
        with self._lock:
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
