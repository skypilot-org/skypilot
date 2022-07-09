"""Logging events to Grafana Loki"""

import click
import contextlib
import datetime
import enum
import json
import os
import time
import traceback
from typing import Dict, Union

import requests

from sky import sky_logging
from sky.utils import common_utils
from sky.utils import env_options
from sky.usage import usage_constants
from sky.usage import utils

logger = sky_logging.init_logger(__name__)

# An indicator for PRIVACY_POLICY has already been shown.
privacy_policy_indicator = os.path.expanduser(
    usage_constants.PRIVACY_POLICY_PATH)
if not env_options.Options.DISABLE_LOGGING.get():
    os.mkdir(os.path.dirname(privacy_policy_indicator), exist_ok=True)
    try:
        with open(privacy_policy_indicator, 'x'):
            click.secho(usage_constants.USAGE_POLICY_MESSAGE, fg='yellow')
    except FileExistsError:
        pass


class MessageType(enum.Enum):
    CLI_CMD = 'cli-cmd'
    TASK_YAML = 'task-yaml'
    TASK_OVERRIDE_YAML = 'task-override-yaml'
    RAY_YAML = 'ray-yaml'
    STACK_TRACE = 'stack-trace'
    RUNTIME = 'runtime'
    # STACK_TRACE and RUNTIME has custom label: 'name'


def _make_labels_str(d):
    dict_str = ','.join(f'{k}="{v}"' for k, v in d.items())
    dict_str = '{' + dict_str + '}'
    return dict_str


def _send_message(msg_type: MessageType,
                  message: str,
                  custom_labels: Dict[str, str] = None):
    """Send the logging to the Grafana Loki.

    The logging message will be a json dict:
    {
        'labels': {
            'user': hash id of user,
            'run_id': run id (same for a single run),
            'time': the uploading time,
            'schema_version': the version of the schema
            **custom_labels: any custom labels provided
        },
        'message': the main content of the logging
    }

    Args:
        msg_type: The type of the logging from the enum
        message: The contents of the logging
        custom_labels: any custom labels for the message
    """
    if env_options.Options.DISABLE_LOGGING.get():
        return

    try:
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
        response = requests.post(usage_constants.LOG_URL,
                                 data=payload,
                                 headers=headers,
                                 timeout=0.5)
        if response.status_code != 204:
            logger.debug(f'Grafana Loki failed with response: {response.text}')
    except (Exception, SystemExit) as e:  # pylint: disable=broad-except
        logger.warning(f'Usage logging exception caught: {e}')


def _clean_yaml(yaml_info: Dict[str, str]):
    """Remove sensitive information from user YAML."""
    cleaned_yaml_info = yaml_info.copy()
    for redact_type in ['setup', 'run', 'envs']:
        if redact_type in cleaned_yaml_info:
            contents = cleaned_yaml_info[redact_type]
            lines = common_utils.dump_yaml_str({
                redact_type: contents
            }).split('\n')
            cleaned_yaml_info[redact_type] = (
                f'{len(lines)} lines {redact_type.upper()}'
                ' redacted')

    return cleaned_yaml_info


def send_cli_cmd():
    """Upload current CLI command to Loki."""
    cmd = common_utils.get_pretty_entry_point()
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
        yaml_info = common_utils.read_yaml(yaml_config_or_path)

    yaml_info = _clean_yaml(yaml_info)
    yaml_info['__redacted_comment_lines'] = len(comment_lines)
    message = json.dumps(yaml_info)
    _send_message(yaml_type, message)


@contextlib.contextmanager
def send_exception_context(name: str):
    if env_options.Options.DISABLE_LOGGING.get():
        yield
        return

    try:
        yield
    except (Exception, SystemExit, KeyboardInterrupt):
        trace = traceback.format_exc()
        _send_message(MessageType.STACK_TRACE,
                      trace,
                      custom_labels={'name': name})
        raise


def send_exception(name_or_fn: str):
    return common_utils.make_decorator(send_exception_context, name_or_fn)


@contextlib.contextmanager
def send_runtime_context(name: str):
    if env_options.Options.DISABLE_LOGGING.get():
        yield
        return

    try:
        start = time.time()
        yield
    finally:
        _send_message(MessageType.RUNTIME,
                      f'{time.time() - start}',
                      custom_labels={'name': name})


def send_runtime(name_or_fn: str):
    return common_utils.make_decorator(send_runtime_context, name_or_fn)
