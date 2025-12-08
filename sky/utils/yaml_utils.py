"""YAML utilities."""
import io
import os
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Union

from sky.adaptors import common
from sky.utils import ux_utils

if TYPE_CHECKING:
    import fsspec
    import yaml
else:
    fsspec = common.LazyImport(
        'fsspec',
        import_error_message=(
            'fsspec is required for loading YAML from URLs or cloud storage. '
            'Install it with: pip install fsspec'))
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


def is_url(path: str) -> bool:
    """Check if a path is a remote URL or cloud storage path.

    Supports: http://, https://, s3://, gs://, az://, etc.

    Args:
        path: Path to check.

    Returns:
        True if path is a remote resource, False if local file.
    """
    # Simple check for common URL schemes (works without fsspec)
    common_schemes = ('http://', 'https://', 's3://', 'gs://', 'az://')
    if any(path.startswith(scheme) for scheme in common_schemes):
        return True

    # For other schemes, try using fsspec if available
    try:
        protocol = fsspec.utils.get_protocol(path)
        return protocol not in ('', 'file')
    except Exception:  # pylint: disable=broad-except
        # If fsspec is not available or fails, assume local file
        return False


def read_file_or_url(path: str) -> str:
    """Read content from a local file, URL, or cloud storage.

    Uses fsspec for URLs and cloud storage. Local files use standard file I/O.

    Args:
        path: Path to read. Examples:
            - Local: /path/to/file.yaml or ~/file.yaml
            - HTTP: https://example.com/file.yaml
            - S3: s3://bucket/path/to/file.yaml
            - GCS: gs://bucket/path/to/file.yaml
            - Azure: az://container/path/to/file.yaml

    Returns:
        The content of the file as a string.

    Raises:
        ValueError: If the file cannot be read.
    """
    try:
        with ux_utils.print_exception_no_traceback():
            # For local files, use standard file I/O (no fsspec needed)
            if not is_url(path):
                path = os.path.expanduser(path)
                with open(path, 'r', encoding='utf-8') as f:
                    return f.read()

            # For URLs and cloud storage, use fsspec
            with fsspec.open(path, 'r') as f:
                return f.read()
    except Exception as e:
        raise ValueError(f'Failed to read {path}: {e}') from e


def read_yaml(path: Optional[str]) -> Dict[str, Any]:
    if path is None:
        raise ValueError('Attempted to read a None YAML.')
    yaml_str = read_file_or_url(path)
    return read_yaml_str(yaml_str)


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
    yaml_str = read_file_or_url(path)
    return read_yaml_all_str(yaml_str)


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
