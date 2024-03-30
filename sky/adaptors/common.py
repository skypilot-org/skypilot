"""Lazy import for modules to avoid import error when not used."""
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
                self.module = __import__(self.module_name)
            except ImportError as e:
                if self._import_error_message is not None:
                    raise ImportError(self._import_error_message) from e
                raise
        return self.module

    def __getattr__(self, name):
        return getattr(self.load_module(), name)
