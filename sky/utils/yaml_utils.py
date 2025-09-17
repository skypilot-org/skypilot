"""YAML utilities."""
import io
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Union

from sky.adaptors import common

if TYPE_CHECKING:
    import yaml
else:
    yaml = common.LazyImport('yaml')

_c_extension_unavailable = False


def safe_load(stream) -> Any:
    global _c_extension_unavailable
    if _c_extension_unavailable:
        return yaml.load(stream, Loader=yaml.SafeLoader)

    try:
        return yaml.load(stream, Loader=yaml.CSafeLoader)
    except AttributeError:
        _c_extension_unavailable = True
        return yaml.load(stream, Loader=yaml.SafeLoader)


def safe_load_all(stream) -> Any:
    global _c_extension_unavailable
    if _c_extension_unavailable:
        return yaml.load_all(stream, Loader=yaml.SafeLoader)

    try:
        return yaml.load_all(stream, Loader=yaml.CSafeLoader)
    except AttributeError:
        _c_extension_unavailable = True
        return yaml.load_all(stream, Loader=yaml.SafeLoader)


def read_yaml(path: Optional[str]) -> Dict[str, Any]:
    if path is None:
        raise ValueError('Attempted to read a None YAML.')
    with open(path, 'r', encoding='utf-8') as f:
        config = safe_load(f)
    return config


def read_yaml_all_str(yaml_str: str) -> List[Dict[str, Any]]:
    stream = io.StringIO(yaml_str)
    config = safe_load_all(stream)
    configs = list(config)
    if not configs:
        # Empty YAML file.
        return [{}]
    return configs


def read_yaml_all(path: str) -> List[Dict[str, Any]]:
    with open(path, 'r', encoding='utf-8') as f:
        return read_yaml_all_str(f.read())


def dump_yaml(path: str,
              config: Union[List[Dict[str, Any]], Dict[str, Any]],
              blank: bool = False) -> None:
    """Dumps a YAML file.

    Args:
        path: the path to the YAML file.
        config: the configuration to dump.
    """
    with open(path, 'w', encoding='utf-8') as f:
        contents = dump_yaml_str(config)
        if blank and isinstance(config, dict) and len(config) == 0:
            # when dumping to yaml, an empty dict will go in as {}.
            contents = ''
        f.write(contents)


def dump_yaml_str(config: Union[List[Dict[str, Any]], Dict[str, Any]]) -> str:
    """Dumps a YAML string.
    Args:
        config: the configuration to dump.
    Returns:
        The YAML string.
    """

    # https://github.com/yaml/pyyaml/issues/127
    class LineBreakDumper(yaml.SafeDumper):

        def write_line_break(self, data=None):
            super().write_line_break(data)
            if len(self.indents) == 1:
                super().write_line_break()

    if isinstance(config, list):
        dump_func = yaml.dump_all  # type: ignore
    else:
        dump_func = yaml.dump  # type: ignore
    return dump_func(config,
                     Dumper=LineBreakDumper,
                     sort_keys=False,
                     default_flow_style=False)
