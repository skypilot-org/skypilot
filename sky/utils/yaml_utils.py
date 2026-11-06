"""YAML utilities."""
import io
import os
import stat
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Union
import uuid

from sky.adaptors import common

if TYPE_CHECKING:
    import yaml
else:
    yaml = common.LazyImport('yaml')

_c_extension_unavailable = False


def check_no_duplicate_keys(yaml_str: str) -> None:
    """Raise ValueError if any mapping in the YAML has duplicate keys.

    PyYAML's default behavior is to silently drop the earlier value on
    duplicate keys, which masks real user typos (e.g. two `name:` lines
    in a task YAML or two mounts with the same remote destination).
    This function walks the YAML node graph and raises a targeted error
    the first time it finds a duplicate, including the line number and
    key name so the user can find it.
    """
    stream = io.StringIO(yaml_str)
    try:
        nodes = list(yaml.compose_all(stream))
    except yaml.YAMLError:
        # Let the regular `safe_load` path produce the user-facing parse
        # error; this function's job is only to catch silent duplicates.
        return

    def walk(node: 'yaml.Node') -> None:
        if isinstance(node, yaml.MappingNode):
            seen: Dict[Any, int] = {}
            for key_node, value_node in node.value:
                # Non-scalar keys (e.g. `? [a, b]`) are legal YAML but not
                # hashable, and SkyPilot schemas don't use them. Skip
                # duplicate detection for them so we don't raise a
                # confusing TypeError.
                if not isinstance(key_node, yaml.ScalarNode):
                    walk(value_node)
                    continue
                key = key_node.value
                if key in seen:
                    prev_line = seen[key] + 1
                    line = key_node.start_mark.line + 1
                    raise ValueError(
                        f'Duplicate key {key!r} in YAML at line {line} '
                        f'(also defined at line {prev_line}). '
                        'Remove one of the entries so the intended value '
                        'is unambiguous.')
                seen[key] = key_node.start_mark.line
                walk(value_node)
        elif isinstance(node, yaml.SequenceNode):
            for child in node.value:
                walk(child)

    for node in nodes:
        if node is not None:
            walk(node)


def _get_safe_loader() -> Any:
    global _c_extension_unavailable
    if not _c_extension_unavailable:
        # LazyImport returns a non-callable submodule proxy for missing
        # attributes, so catching AttributeError does not detect a missing
        # C extension. Select the loader before parsing (load_all is lazy).
        loader = getattr(yaml, 'CSafeLoader', None)
        if callable(loader):
            return loader
        _c_extension_unavailable = True
    return yaml.SafeLoader


def safe_load(stream) -> Any:
    return yaml.load(stream, Loader=_get_safe_loader())


def safe_load_all(stream) -> Any:
    return yaml.load_all(stream, Loader=_get_safe_loader())


def read_yaml(path: Optional[str]) -> Dict[str, Any]:
    if path is None:
        raise ValueError('Attempted to read a None YAML.')
    with open(path, 'r', encoding='utf-8') as f:
        config = safe_load(f)
    return config


def read_yaml_str(yaml_str: str) -> Dict[str, Any]:
    stream = io.StringIO(yaml_str)
    parsed_yaml = safe_load(stream)
    if not parsed_yaml:
        # Empty dict
        return {}
    return parsed_yaml


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


def dump_yaml_atomic(path: str, config: Dict[str, Any]) -> None:
    """Dumps a YAML file by replacing it: a concurrent reader sees the old or
    the new contents, never a truncated file."""
    contents = dump_yaml_str(config)
    # Replace the resolved path, so a symlinked file keeps its link.
    real_path = os.path.realpath(path)
    try:
        mode: Optional[int] = stat.S_IMODE(os.stat(real_path).st_mode)
    except FileNotFoundError:
        mode = None
    tmp_path = os.path.join(os.path.dirname(real_path),
                            f'.{uuid.uuid4().hex}.tmp')
    # 0o666 under the umask, as open(path, 'w') would create a new file.
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    replaced = False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            if mode is not None:
                # Before writing, so the contents are never more readable.
                os.fchmod(f.fileno(), mode)
            f.write(contents)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, real_path)
        replaced = True
    finally:
        if not replaced:
            os.remove(tmp_path)


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
