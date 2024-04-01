"""Lazy import for modules to avoid import error when not used."""
import importlib
from typing import Optional


class LazyImport:
    """Lazy importer for heavy modules."""

    def __init__(self, module_name, import_error_message: Optional[str] = None):
        self.module_name = module_name
        self.module = None
        self._import_error_message = import_error_message

    def load_module(self):
        if self.module is None:
            try:
                self.module = importlib.import_module(self.module_name)
            except ImportError as e:
                if self._import_error_message is not None:
                    raise ImportError(self._import_error_message) from e
                raise
        return self.module

    def __getattr__(self, name):
        # Attempt to access the attribute, if it fails, assume it's a submodule
        # and lazily import it
        try:
            return getattr(self.load_module(), name)
        except AttributeError:
            # Dynamically create a new LazyImport instance for the submodule
            submodule_name = f'{self.module_name}.{name}'
            setattr(self, name,
                    LazyImport(submodule_name, self._import_error_message))
            return getattr(self, name)
