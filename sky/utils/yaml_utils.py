"""YAML utilities."""
from typing import Any, TYPE_CHECKING

from sky.adaptors import common

if TYPE_CHECKING:
    import yaml
else:
    yaml = common.LazyImport('yaml')

_csafe_loader_import_error = False


def safe_load(stream) -> Any:
    global _csafe_loader_import_error
    if _csafe_loader_import_error:
        return yaml.load(stream, Loader=yaml.SafeLoader)

    try:
        return yaml.load(stream, Loader=yaml.CSafeLoader)
    except ImportError:
        _csafe_loader_import_error = True
        return yaml.load(stream, Loader=yaml.SafeLoader)


def safe_load_all(stream) -> Any:
    global _csafe_loader_import_error
    if _csafe_loader_import_error:
        return yaml.load_all(stream, Loader=yaml.SafeLoader)

    try:
        return yaml.load_all(stream, Loader=yaml.CSafeLoader)
    except ImportError:
        _csafe_loader_import_error = True
        return yaml.load_all(stream, Loader=yaml.SafeLoader)
