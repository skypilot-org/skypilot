"""Logging events to Grafana Loki"""

import datetime
import enum
import functools
import json
import time
import traceback
from typing import Dict, Union

import requests

from sky import sky_logging
from sky.utils import base_utils
from sky.utils import env_options
from sky.user_stats import utils

logger = sky_logging.init_logger(__name__)

_LOG_URL = 'https://178762:eyJrIjoiN2VhYWQ3YWRkNzM0NDY0ZmE4YmRlNzRhYTk2ZGRhOWQ5ZjdkMGE0ZiIsIm4iOiJza3lwaWxvdC11c2VyLXN0YXRzLW1ldHJpY3MiLCJpZCI6NjE1MDQ2fQ=@logs-prod3.grafana.net/api/prom/push'  # pylint: disable=line-too-long
log_timestamp = None


class MessageType(enum.Enum):
    CLI_CMD = 'cli-cmd'
    STACK_TRACE = 'stack-trace'
    TASK_YAML = 'task-yaml'
    TASK_OVERRIDE_YAML = 'task-override-yaml'
    RAY_YAML = 'ray-yaml'
    RUNTIME = 'runtime'


def _make_labels_str(d):
    dict_str = ','.join(f'{k}="{v}"' for k, v in d.items())
    dict_str = '{' + dict_str + '}'
    return dict_str


def _send_message(msg_type: MessageType,
                  message: str,
                  custom_labels: Dict[str, str] = None):
    if env_options.DISABLE_LOGGING:
        return
    global log_timestamp
    if log_timestamp is None:
        log_timestamp = datetime.datetime.now(
            datetime.timezone.utc).isoformat('T')

    if custom_labels is None:
        custom_labels = dict()
    custom_labels.update(utils.get_base_labels())

    prom_labels = {'type': msg_type.value}
    message = {
        'labels': custom_labels,
        'message': message,
    }

    headers = {'Content-type': 'application/json'}
    payload = {
        'streams': [{
            'labels': _make_labels_str(prom_labels),
            'entries': [{
                'ts': log_timestamp,
                'line': json.dumps(message),
            }]
        }]
    }
    payload = json.dumps(payload)
    response = requests.post(_LOG_URL, data=payload, headers=headers)
    if response.status_code != 204:
        logger.debug(f'Grafana Loki failed with response: {response.text}')


def _clean_yaml(yaml_info: Dict[str, str], num_comment_lines: int):
    """Remove sensitive information from user YAML."""
    cleaned_yaml_info = yaml_info.copy()
    for redact_type in ['setup', 'run', 'envs']:
        if redact_type in cleaned_yaml_info:
            contents = cleaned_yaml_info[redact_type]
            lines = base_utils.dump_yaml_str({
                redact_type: contents
            }).split('\n')
            cleaned_yaml_info[redact_type] = (
                f'{len(lines)} lines {redact_type.upper()}'
                ' redacted')

    cleaned_yaml_info['__redacted_comment_lines'] = num_comment_lines

    return cleaned_yaml_info


def send_cli_cmd():
    """Upload current CLI command to Loki."""
    cmd = base_utils.get_pretty_entry_point()
    _send_message(MessageType.CLI_CMD, cmd)


def send_yaml(yaml_config_or_path: Union[Dict, str], yaml_type: MessageType):
    """Upload safe contents of YAML file to Loki."""
    if isinstance(yaml_config_or_path, dict):
        yaml_info = yaml_config_or_path
        comment_lines = []
    else:
        with open(yaml_config_or_path, 'r') as f:
            lines = f.readlines()
            comment_lines = [line for line in lines if line.startswith('#')]
        yaml_info = base_utils.read_yaml(yaml_config_or_path)
    yaml_info = _clean_yaml(yaml_info, len(comment_lines))
    message = json.dumps(yaml_info)
    _send_message(yaml_type, message)


def send_exception(name: str):
    """Decorator to catch exceptions and upload to usage logging."""
    if env_options.DISABLE_LOGGING:
        return lambda func: func

    def _send_exception(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except (Exception, SystemExit, KeyboardInterrupt):
                trace = traceback.format_exc()
                _send_message(MessageType.STACK_TRACE,
                              trace,
                              custom_labels={'name': name})
                raise
        return wrapper

    return _send_exception

def send_runtime(name: str):
    """Decorator to log runtime of function."""
    if env_options.DISABLE_LOGGING:
        return lambda func: func

    def _send_runtime(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                start = time.time()
                return func(*args, **kwargs)
            finally:
                _send_message(MessageType.RUNTIME,
                              f'{time.time() - start}',
                              custom_labels={'name': name})
        return wrapper
    return _send_runtime
