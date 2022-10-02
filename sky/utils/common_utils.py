"""Utils shared between all of sky"""

import functools
import getpass
import inspect
import hashlib
import json
import random
import os
import re
import socket
import sys
import time
import uuid
from typing import Dict, List, Union
import yaml

from sky import sky_logging

_USER_HASH_FILE = os.path.expanduser('~/.sky/user_hash')

_PAYLOAD_PATTERN = re.compile(r'<sky-payload>(.*)</sky-payload>')
_PAYLOAD_STR = '<sky-payload>{}</sky-payload>'

logger = sky_logging.init_logger(__name__)

_run_id = None


def get_run_id():
    """Returns a unique run id for each 'run'.

    A run is defined as the lifetime of a process that has imported `sky`
    and has called its CLI or programmatic APIs. For example, two successive
    `sky launch` are two runs.
    """
    global _run_id
    if _run_id is None:
        _run_id = str(uuid.uuid4())
    return _run_id


def get_user_hash():
    """Returns a unique user-machine specific hash as a user id."""
    if os.path.exists(_USER_HASH_FILE):
        with open(_USER_HASH_FILE, 'r') as f:
            return f.read()

    hash_str = user_and_hostname_hash()
    user_hash = hashlib.md5(hash_str.encode()).hexdigest()[:8]
    os.makedirs(os.path.dirname(_USER_HASH_FILE), exist_ok=True)
    with open(_USER_HASH_FILE, 'w') as f:
        f.write(user_hash)
    return user_hash


class Backoff:
    """Exponential backoff with jittering."""
    MULTIPLIER = 1.6
    JITTER = 0.4

    def __init__(self, initial_backoff: int = 5, max_backoff_factor: int = 5):
        self._initial = True
        self._backoff = None
        self._inital_backoff = initial_backoff
        self._max_backoff = max_backoff_factor * self._inital_backoff

    # https://github.com/grpc/grpc/blob/2d4f3c56001cd1e1f85734b2f7c5ce5f2797c38a/doc/connection-backoff.md
    # https://github.com/grpc/grpc/blob/5fc3ff82032d0ebc4bf252a170ebe66aacf9ed9d/src/core/lib/backoff/backoff.cc

    def current_backoff(self) -> float:
        """Backs off once and returns the current backoff in seconds."""
        if self._initial:
            self._initial = False
            self._backoff = min(self._inital_backoff, self._max_backoff)
        else:
            self._backoff = min(self._backoff * self.MULTIPLIER,
                                self._max_backoff)
        self._backoff += random.uniform(-self.JITTER * self._backoff,
                                        self.JITTER * self._backoff)
        return self._backoff


def get_pretty_entry_point() -> str:
    """Returns the prettified entry point of this process (sys.argv).

    Example return values:
        $ sky launch app.yaml  # 'sky launch app.yaml'
        $ sky gpunode  # 'sky gpunode'
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


def read_yaml(path):
    with open(path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def dump_yaml(path, config):
    with open(path, 'w') as f:
        f.write(dump_yaml_str(config))


def dump_yaml_str(config):
    # https://github.com/yaml/pyyaml/issues/127
    class LineBreakDumper(yaml.SafeDumper):

        def write_line_break(self, data=None):
            super().write_line_break(data)
            if len(self.indents) == 1:
                super().write_line_break()

    return yaml.dump(config,
                     Dumper=LineBreakDumper,
                     sort_keys=False,
                     default_flow_style=False)


def make_decorator(cls, name_or_fn, **ctx_kwargs):
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
                nonlocal name_or_fn
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
            nonlocal name_or_fn
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
                logger.warning(f'Caught {e}. Retrying.')
                try_count += 1
                if try_count < max_retries:
                    time.sleep(backoff.current_backoff())
                else:
                    raise

    return method_with_retries


def encode_payload(payload: Union[List, Dict]) -> str:
    """Encode a payload to make it more robust for parsing.

    Args:
        payload: A dict or list to be encoded.

    Returns:
        A string that is encoded from the payload.
    """
    payload_str = json.dumps(payload)
    payload_str = _PAYLOAD_STR.format(payload_str)
    return payload_str


def decode_payload(payload_str: str) -> Union[List, Dict]:
    """Decode a payload string.

    Args:
        payload_str: A string that is encoded from a payload.

    Returns:
        A dict or list that is decoded from the payload string.
    """
    payload_str = _PAYLOAD_PATTERN.match(payload_str).group(1)
    payload = json.loads(payload_str)
    return payload
