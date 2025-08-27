"""YAML utilities."""
from typing import Any, TYPE_CHECKING

from sky.adaptors import common

if TYPE_CHECKING:
    import yaml
else:
    yaml = common.LazyImport('yaml')

_csafe_loader_unavailable = False


def safe_load(stream) -> Any:
    global _csafe_loader_unavailable
    if _csafe_loader_unavailable:
        return yaml.load(stream, Loader=yaml.SafeLoader)

    try:
        return yaml.load(stream, Loader=yaml.CSafeLoader)
    except AttributeError:
        _csafe_loader_unavailable = True
        return yaml.load(stream, Loader=yaml.SafeLoader)


def safe_load_all(stream) -> Any:
    global _csafe_loader_unavailable
    if _csafe_loader_unavailable:
        return yaml.load_all(stream, Loader=yaml.SafeLoader)

    try:
        return yaml.load_all(stream, Loader=yaml.CSafeLoader)
    except AttributeError:
        _csafe_loader_unavailable = True
        return yaml.load_all(stream, Loader=yaml.SafeLoader)
