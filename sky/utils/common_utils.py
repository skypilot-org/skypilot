"""Utils shared between all of sky"""

import difflib
import functools
import getpass
import hashlib
import inspect
import io
import os
import platform
import random
import re
import socket
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Union
import uuid

import jinja2
import jsonschema
import psutil
import yaml

from sky import exceptions
from sky import sky_logging
from sky.skylet import constants
from sky.usage import constants as usage_constants
from sky.utils import annotations
from sky.utils import ux_utils
from sky.utils import validator

_USER_HASH_FILE = os.path.expanduser('~/.sky/user_hash')
USER_HASH_LENGTH = 8

# We are using base36 to reduce the length of the hash. 2 chars -> 36^2 = 1296
# possibilities. considering the final cluster name contains the prefix as well,
# we should be fine with 2 chars.
CLUSTER_NAME_HASH_LENGTH = 2

_COLOR_PATTERN = re.compile(r'\x1b[^m]*m')

_VALID_ENV_VAR_REGEX = '[a-zA-Z_][a-zA-Z0-9_]*'

logger = sky_logging.init_logger(__name__)


@annotations.lru_cache(scope='request')
def get_usage_run_id() -> str:
    """Returns a unique run id for each 'run'.

    A run is defined as the lifetime of a process that has imported `sky`
    and has called its CLI or programmatic APIs. For example, two successive
    `sky launch` are two runs.
    """
    usage_run_id = os.getenv(usage_constants.USAGE_RUN_ID_ENV_VAR)
    if usage_run_id is not None:
        return usage_run_id
    return str(uuid.uuid4())


def _is_valid_user_hash(user_hash: Optional[str]) -> bool:
    if user_hash is None:
        return False
    try:
        int(user_hash, 16)
    except (TypeError, ValueError):
        return False
    return len(user_hash) == USER_HASH_LENGTH


def generate_user_hash() -> str:
    """Generates a unique user-machine specific hash."""
    hash_str = user_and_hostname_hash()
    user_hash = hashlib.md5(hash_str.encode()).hexdigest()[:USER_HASH_LENGTH]
    if not _is_valid_user_hash(user_hash):
        # A fallback in case the hash is invalid.
        user_hash = uuid.uuid4().hex[:USER_HASH_LENGTH]
    return user_hash


def get_user_hash() -> str:
    """Returns a unique user-machine specific hash as a user id.

    We cache the user hash in a file to avoid potential user_name or
    hostname changes causing a new user hash to be generated.
    """
    user_hash = os.getenv(constants.USER_ID_ENV_VAR)
    if _is_valid_user_hash(user_hash):
        assert user_hash is not None
        return user_hash

    if os.path.exists(_USER_HASH_FILE):
        # Read from cached user hash file.
        with open(_USER_HASH_FILE, 'r', encoding='utf-8') as f:
            # Remove invalid characters.
            user_hash = f.read().strip()
        if _is_valid_user_hash(user_hash):
            return user_hash

    user_hash = generate_user_hash()
    os.makedirs(os.path.dirname(_USER_HASH_FILE), exist_ok=True)
    with open(_USER_HASH_FILE, 'w', encoding='utf-8') as f:
        f.write(user_hash)
    return user_hash


def base36_encode(hex_str: str) -> str:
    """Converts a hex string to a base36 string."""
    int_value = int(hex_str, 16)

    def _base36_encode(num: int) -> str:
        if num == 0:
            return '0'
        alphabet = '0123456789abcdefghijklmnopqrstuvwxyz'
        base36 = ''
        while num != 0:
            num, i = divmod(num, 36)
            base36 = alphabet[i] + base36
        return base36

    return _base36_encode(int_value)


def check_cluster_name_is_valid(cluster_name: Optional[str]) -> None:
    """Errors out on invalid cluster names.

    Bans (including but not limited to) names that:
    - are digits-only
    - start with invalid character, like hyphen

    Raises:
        exceptions.InvalidClusterNameError: If the cluster name is invalid.
    """
    if cluster_name is None:
        return
    valid_regex = constants.CLUSTER_NAME_VALID_REGEX
    if re.fullmatch(valid_regex, cluster_name) is None:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.InvalidClusterNameError(
                f'Cluster name "{cluster_name}" is invalid; '
                'ensure it is fully matched by regex (e.g., '
                'only contains letters, numbers and dash): '
                f'{valid_regex}')


def make_cluster_name_on_cloud(display_name: str,
                               max_length: Optional[int] = 15,
                               add_user_hash: bool = True) -> str:
    """Generate valid cluster name on cloud that is unique to the user.

    This is to map the cluster name to a valid length and character set for
    cloud providers,
    - e.g. GCP limits the length of the cluster name to 35 characters. If the
    cluster name with user hash is longer than max_length:
      1. Truncate it to max_length - cluster_hash - user_hash_length.
      2. Append the hash of the cluster name
    - e.g. some cloud providers don't allow for uppercase letters, periods,
    or underscores, so we convert it to lower case and replace those
    characters with hyphens

    Args:
        display_name: The cluster name to be truncated, hashed, and
            transformed.
        max_length: The maximum length of the cluster name. If None, no
            truncation is performed.
        add_user_hash: Whether to append user hash to the cluster name.
    """

    cluster_name_on_cloud = re.sub(r'[._]', '-', display_name).lower()
    if display_name != cluster_name_on_cloud:
        logger.debug(
            f'The user specified cluster name {display_name} might be invalid '
            f'on the cloud, we convert it to {cluster_name_on_cloud}.')
    user_hash = ''
    if add_user_hash:
        user_hash = get_user_hash()
        user_hash = f'-{user_hash}'
    user_hash_length = len(user_hash)

    if (max_length is None or
            len(cluster_name_on_cloud) <= max_length - user_hash_length):
        return f'{cluster_name_on_cloud}{user_hash}'
    # -1 is for the dash between cluster name and cluster name hash.
    truncate_cluster_name_length = (max_length - CLUSTER_NAME_HASH_LENGTH - 1 -
                                    user_hash_length)
    truncate_cluster_name = cluster_name_on_cloud[:truncate_cluster_name_length]
    if truncate_cluster_name.endswith('-'):
        truncate_cluster_name = truncate_cluster_name.rstrip('-')
    assert truncate_cluster_name_length > 0, (cluster_name_on_cloud, max_length)
    display_name_hash = hashlib.md5(display_name.encode()).hexdigest()
    # Use base36 to reduce the length of the hash.
    display_name_hash = base36_encode(display_name_hash)
    return (f'{truncate_cluster_name}'
            f'-{display_name_hash[:CLUSTER_NAME_HASH_LENGTH]}{user_hash}')


def cluster_name_in_hint(cluster_name: str, cluster_name_on_cloud: str) -> str:
    if cluster_name_on_cloud.startswith(cluster_name):
        return repr(cluster_name)
    return f'{cluster_name!r} (name on cloud: {cluster_name_on_cloud!r})'


def get_global_job_id(job_timestamp: str,
                      cluster_name: Optional[str],
                      job_id: str,
                      task_id: Optional[int] = None,
                      is_managed_job: bool = False) -> str:
    """Returns a unique job run id for each job run.

    A job run is defined as the lifetime of a job that has been launched.
    """
    managed_job_str = 'managed-' if is_managed_job else ''
    _, sep, timestamp = job_timestamp.partition('sky-')
    job_timestamp = f'{sep}{managed_job_str}{timestamp}'
    global_job_id = f'{job_timestamp}_{cluster_name}_{job_id}'
    if task_id is not None:
        global_job_id += f'-{task_id}'
    return global_job_id


class Backoff:
    """Exponential backoff with jittering."""
    MULTIPLIER = 1.6
    JITTER = 0.4

    def __init__(self, initial_backoff: float = 5, max_backoff_factor: int = 5):
        self._initial = True
        self._backoff = 0.0
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff_factor * self._initial_backoff

    # https://github.com/grpc/grpc/blob/2d4f3c56001cd1e1f85734b2f7c5ce5f2797c38a/doc/connection-backoff.md
    # https://github.com/grpc/grpc/blob/5fc3ff82032d0ebc4bf252a170ebe66aacf9ed9d/src/core/lib/backoff/backoff.cc

    def current_backoff(self) -> float:
        """Backs off once and returns the current backoff in seconds."""
        if self._initial:
            self._initial = False
            self._backoff = min(self._initial_backoff, self._max_backoff)
        else:
            self._backoff = min(self._backoff * self.MULTIPLIER,
                                self._max_backoff)
        self._backoff += random.uniform(-self.JITTER * self._backoff,
                                        self.JITTER * self._backoff)
        return self._backoff


_current_command: Optional[str] = None
_current_client_entrypoint: Optional[str] = None


def set_client_entrypoint_and_command(client_entrypoint: Optional[str],
                                      client_command: Optional[str]):
    """Override the current client entrypoint and command.

    This is useful when we are on the SkyPilot API server side and we have a
    client entrypoint and command from the client.
    """
    global _current_command, _current_client_entrypoint
    _current_command = client_command
    _current_client_entrypoint = client_entrypoint


def get_current_command() -> str:
    """Returns the command related to this operation.

    Normally uses get_pretty_entry_point(), but will use the client command on
    the server side.
    """
    if _current_command is not None:
        return _current_command

    return get_pretty_entrypoint_cmd()


def get_current_client_entrypoint(server_entrypoint: str) -> str:
    """Returns the current client entrypoint.

    Gets the client entrypoint from the context, if it is not set, returns the
    server entrypoint.
    """
    if _current_client_entrypoint is not None:
        return _current_client_entrypoint
    return server_entrypoint


def get_pretty_entrypoint_cmd() -> str:
    """Returns the prettified entry point of this process (sys.argv).

    Example return values:
        $ sky launch app.yaml  # 'sky launch app.yaml'
        $ python examples/app.py  # 'app.py'
    """
    argv = sys.argv
    basename = os.path.basename(argv[0])
    if basename == 'sky':
        # Turn '/.../anaconda/envs/py36/bin/sky' into 'sky', but keep other
        # things like 'examples/app.py'.
        argv[0] = basename
    return ' '.join(argv)


def user_and_hostname_hash() -> str:
    """Returns a string containing <user>-<hostname hash last 4 chars>.

    For uniquefying user clusters on shared-account cloud providers. Also used
    for AWS security group.

    Using uuid.getnode() instead of gethostname() is incorrect; observed to
    collide on Macs.

    NOTE: BACKWARD INCOMPATIBILITY NOTES

    Changing this string will render AWS clusters shown in `sky status`
    unreusable and potentially cause leakage:

    - If a cluster is STOPPED, any command restarting it (`sky launch`, `sky
      start`) will launch a NEW cluster.
    - If a cluster is UP, a `sky launch` command reusing it will launch a NEW
      cluster. The original cluster will be stopped and thus leaked from Sky's
      perspective.
    - `sky down/stop/exec` on these pre-change clusters still works, if no new
      clusters with the same name have been launched.

    The reason is AWS security group names are derived from this string, and
    thus changing the SG name makes these clusters unrecognizable.
    """
    hostname_hash = hashlib.md5(socket.gethostname().encode()).hexdigest()[-4:]
    return f'{getpass.getuser()}-{hostname_hash}'


def read_yaml(path: Optional[str]) -> Dict[str, Any]:
    if path is None:
        raise ValueError('Attempted to read a None YAML.')
    with open(path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config


def read_yaml_all_str(yaml_str: str) -> List[Dict[str, Any]]:
    stream = io.StringIO(yaml_str)
    config = yaml.safe_load_all(stream)
    configs = list(config)
    if not configs:
        # Empty YAML file.
        return [{}]
    return configs


def read_yaml_all(path: str) -> List[Dict[str, Any]]:
    with open(path, 'r', encoding='utf-8') as f:
        return read_yaml_all_str(f.read())


def dump_yaml(path: str, config: Union[List[Dict[str, Any]],
                                       Dict[str, Any]]) -> None:
    """Dumps a YAML file.

    Args:
        path: the path to the YAML file.
        config: the configuration to dump.
    """
    with open(path, 'w', encoding='utf-8') as f:
        f.write(dump_yaml_str(config))


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


def make_decorator(cls, name_or_fn: Union[str, Callable],
                   **ctx_kwargs) -> Callable:
    """Make the cls a decorator.

    class cls:
        def __init__(self, name, **kwargs):
            pass
        def __enter__(self):
            pass
        def __exit__(self, exc_type, exc_value, traceback):
            pass

    Args:
        name_or_fn: The name of the event or the function to be wrapped.
        message: The message attached to the event.
    """
    if isinstance(name_or_fn, str):

        def _wrapper(f):

            @functools.wraps(f)
            def _record(*args, **kwargs):
                with cls(name_or_fn, **ctx_kwargs):
                    return f(*args, **kwargs)

            return _record

        return _wrapper
    else:
        if not inspect.isfunction(name_or_fn):
            raise ValueError(
                'Should directly apply the decorator to a function.')

        @functools.wraps(name_or_fn)
        def _record(*args, **kwargs):
            f = name_or_fn
            func_name = getattr(f, '__qualname__', f.__name__)
            module_name = getattr(f, '__module__', '')
            if module_name:
                full_name = f'{module_name}.{func_name}'
            else:
                full_name = func_name
            with cls(full_name, **ctx_kwargs):
                return f(*args, **kwargs)

        return _record


def retry(method, max_retries=3, initial_backoff=1):
    """Retry a function up to max_retries times with backoff between retries."""

    @functools.wraps(method)
    def method_with_retries(*args, **kwargs):
        backoff = Backoff(initial_backoff)
        try_count = 0
        while try_count < max_retries:
            try:
                return method(*args, **kwargs)
            except Exception as e:  # pylint: disable=broad-except
                try_count += 1
                if try_count < max_retries:
                    logger.warning(f'Caught {e}. Retrying.')
                    time.sleep(backoff.current_backoff())
                else:
                    raise

    return method_with_retries


def class_fullname(cls, skip_builtins: bool = True):
    """Get the full name of a class.

    Example:
        >>> e = sky.exceptions.FetchClusterInfoError()
        >>> class_fullname(e.__class__)
        'sky.exceptions.FetchClusterInfoError'

    Args:
        cls: The class to get the full name.

    Returns:
        The full name of the class.
    """
    module_name = getattr(cls, '__module__', '')
    if not module_name or (module_name == 'builtins' and skip_builtins):
        return cls.__name__
    return f'{cls.__module__}.{cls.__name__}'


def format_exception(e: Union[Exception, SystemExit, KeyboardInterrupt],
                     use_bracket: bool = False) -> str:
    """Format an exception to a string.

    Args:
        e: The exception to format.

    Returns:
        A string that represents the exception.
    """
    if use_bracket:
        return f'[{class_fullname(e.__class__)}] {e}'
    return f'{class_fullname(e.__class__)}: {e}'


def remove_color(s: str):
    """Remove color from a string.

    Args:
        s: The string to remove color.

    Returns:
        A string without color.
    """
    return _COLOR_PATTERN.sub('', s)


def remove_file_if_exists(path: Optional[str]):
    """Delete a file if it exists.

    Args:
        path: The path to the file.
    """
    if path is None:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        logger.debug(f'Tried to remove {path} but failed to find it. Skip.')
        pass


def is_wsl() -> bool:
    """Detect if running under Windows Subsystem for Linux (WSL)."""
    return 'microsoft' in platform.uname()[3].lower()


def find_free_port(start_port: int) -> int:
    """Finds first free local port starting with 'start_port'.

    Returns: a free local port.

    Raises:
      OSError: If no free ports are available.
    """
    for port in range(start_port, 65535):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('', port))
                return port
            except OSError:
                pass
    raise OSError('No free ports available.')


def is_valid_env_var(name: str) -> bool:
    """Checks if the task environment variable name is valid."""
    return bool(re.fullmatch(_VALID_ENV_VAR_REGEX, name))


def format_float(num: Union[float, int], precision: int = 1) -> str:
    """Formats a float to not show decimal point if it is a whole number

    If it is not a whole number, it will show upto precision decimal point."""
    if isinstance(num, int):
        return str(num)
    return '{:.0f}'.format(num) if num.is_integer() else f'{num:.{precision}f}'


def validate_schema(obj, schema, err_msg_prefix='', skip_none=True):
    """Validates an object against a given JSON schema.

    Args:
        obj: The object to validate.
        schema: The JSON schema against which to validate the object.
        err_msg_prefix: The string to prepend to the error message if
          validation fails.
        skip_none: If True, removes fields with value None from the object
          before validation. This is useful for objects that will never contain
          None because yaml.safe_load() loads empty fields as None.

    Raises:
        ValueError: if the object does not match the schema.
    """
    if skip_none:
        obj = {k: v for k, v in obj.items() if v is not None}
    err_msg = None
    try:
        validator.SchemaValidator(schema).validate(obj)
    except jsonschema.ValidationError as e:
        if e.validator == 'additionalProperties':
            if tuple(e.schema_path) == ('properties', 'envs',
                                        'additionalProperties'):
                # Hack. Here the error is Task.envs having some invalid keys. So
                # we should not print "unsupported field".
                #
                # This will print something like:
                # 'hello world' does not match any of the regexes: <regex>
                err_msg = (err_msg_prefix +
                           'The `envs` field contains invalid keys:\n' +
                           e.message)
            else:
                err_msg = err_msg_prefix
                assert isinstance(e.schema, dict), 'Schema must be a dictionary'
                known_fields = set(e.schema.get('properties', {}).keys())
                assert isinstance(e.instance,
                                  dict), 'Instance must be a dictionary'
                for field in e.instance:
                    if field not in known_fields:
                        most_similar_field = difflib.get_close_matches(
                            field, known_fields, 1)
                        if most_similar_field:
                            err_msg += (f'Instead of {field!r}, did you mean '
                                        f'{most_similar_field[0]!r}?')
                        else:
                            err_msg += f'Found unsupported field {field!r}.'
        else:
            message = e.message
            # Object in jsonschema is represented as dict in Python. Replace
            # 'object' with 'dict' for better readability.
            message = message.replace('type \'object\'', 'type \'dict\'')
            # Example e.json_path value: '$.resources'
            err_msg = (err_msg_prefix + message +
                       f'. Check problematic field(s): {e.json_path}')

    if err_msg:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.InvalidSkyPilotConfigError(err_msg)


def get_cleaned_username(username: str = '') -> str:
    """Cleans the username. Underscores are allowed, as we will
     handle it when mapping to the cluster_name_on_cloud in
     common_utils.make_cluster_name_on_cloud.

    Clean up includes:
     1. Making all characters lowercase
     2. Removing any non-alphanumeric characters (excluding hyphens and
        underscores)
     3. Removing any numbers and/or hyphens at the start of the username.
     4. Removing any hyphens at the end of the username
     5. Truncate the username to 63 characters, as requested by GCP labels

    Dots are removed due to: https://cloud.google.com/compute/docs/labeling-resources#requirements # pylint: disable=line-too-long

    e.g. 1SkY-PiLot2- becomes sky-pilot2

    Returns:
      A cleaned username.
    """
    username = username or getpass.getuser()
    username = username.lower()
    username = re.sub(r'[^a-z0-9-_]', '', username)
    username = re.sub(r'^[0-9-]+', '', username)
    username = re.sub(r'-$', '', username)
    username = username[:63]
    return username


def fill_template(template_name: str, variables: Dict[str, Any],
                  output_path: str) -> None:
    """Create a file from a Jinja template and return the filename."""
    assert template_name.endswith('.j2'), template_name
    root_dir = os.path.dirname(os.path.dirname(__file__))
    template_path = os.path.join(root_dir, 'templates', template_name)
    if not os.path.exists(template_path):
        raise FileNotFoundError(f'Template "{template_name}" does not exist.')
    with open(template_path, 'r', encoding='utf-8') as fin:
        template = fin.read()
    output_path = os.path.abspath(os.path.expanduser(output_path))
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Write out yaml config.
    j2_template = jinja2.Template(template)
    content = j2_template.render(**variables)
    with open(output_path, 'w', encoding='utf-8') as fout:
        fout.write(content)


def deprecated_function(
        func: Callable,
        name: str,
        deprecated_name: str,
        removing_version: str,
        override_argument: Optional[Dict[str, Any]] = None) -> Callable:
    """Decorator for creating deprecated functions, for backward compatibility.

    It will result in a warning being emitted when the function is used.
    """

    @functools.wraps(func)
    def new_func(*args, **kwargs):
        override_argument_str = ''
        if override_argument:
            override_argument_str = ', '.join(
                f'{k}={v}' for k, v in override_argument.items())
        logger.warning(
            f'Call to deprecated function {deprecated_name}, which will be '
            f'removed in {removing_version}. Please use '
            f'{name}({override_argument_str}) instead.')
        return func(*args, **kwargs)

    return new_func


def truncate_long_string(s: str, max_length: int = 35) -> str:
    """Truncate a string to a maximum length, preserving whole words."""
    if len(s) <= max_length:
        return s
    splits = s.split(' ')
    if len(splits[0]) > max_length:
        return splits[0][:max_length] + '...'  # Use '…'?
    # Truncate on word boundary.
    i = 0
    total = 0
    for i, part in enumerate(splits):
        total += len(part)
        if total >= max_length:
            break
    prefix = ' '.join(splits[:i])
    if len(prefix) < max_length:
        prefix += s[len(prefix):max_length]
    return prefix + '...'


def hash_file(path: str, hash_alg: str) -> 'hashlib._Hash':
    # In python 3.11, hashlib.file_digest is available, but for <3.11 we have to
    # do it manually.
    # This implementation is simplified from the implementation in CPython.
    # TODO(cooperc): Use hashlib.file_digest once we move to 3.11+.
    # Beware of f.read() as some files may be larger than memory.
    with open(path, 'rb') as f:
        file_hash = hashlib.new(hash_alg)
        buf = bytearray(2**18)
        view = memoryview(buf)
        while True:
            size = f.readinto(buf)
            if size == 0:
                # EOF
                break
            file_hash.update(view[:size])
        return file_hash


def is_port_available(port: int, reuse_addr: bool = True) -> bool:
    """Check if a TCP port is available for binding on localhost.

    Args:
        port: The port number to check.
        reuse_addr: If True, sets SO_REUSEADDR socket option to allow reusing
            ports in TIME_WAIT state. Servers like multiprocessing.Manager set
            SO_REUSEADDR by default to accelerate restart. The option should be
            coordinated in check.

    Returns:
        bool: True if the port is available for binding, False otherwise.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if reuse_addr:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('localhost', port))
            return True
        except OSError:
            return False


# TODO(aylei): should be aware of cgroups
def get_cpu_count() -> int:
    """Get the number of CPUs.

    If the API server is deployed as a pod in k8s cluster, we assume the
    number of CPUs is provided by the downward API.
    """
    cpu_count = os.getenv('SKYPILOT_POD_CPU_CORE_LIMIT')
    if cpu_count is not None:
        try:
            return int(float(cpu_count))
        except ValueError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Failed to parse the number of CPUs from {cpu_count}'
                ) from e
    return psutil.cpu_count()


# TODO(aylei): should be aware of cgroups
def get_mem_size_gb() -> float:
    """Get the memory size in GB.

    If the API server is deployed as a pod in k8s cluster, we assume the
    memory size is provided by the downward API.
    """
    mem_size = os.getenv('SKYPILOT_POD_MEMORY_GB_LIMIT')
    if mem_size is not None:
        try:
            return float(mem_size)
        except ValueError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Failed to parse the memory size from {mem_size}') from e
    return psutil.virtual_memory().total / (1024**3)
