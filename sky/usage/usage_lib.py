"""Logging events to Grafana Loki"""

import enum
import click
import contextlib
import datetime
import json
import os
import time
import traceback
from typing import Any, Dict, List, Optional, Union

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
    os.makedirs(os.path.dirname(privacy_policy_indicator), exist_ok=True)
    try:
        with open(privacy_policy_indicator, 'x'):
            click.secho(usage_constants.USAGE_POLICY_MESSAGE, fg='yellow')
    except FileExistsError:
        pass


class MessageType(enum.Enum):
    """Types for messages to be sent to Loki."""
    USAGE = 'usage'
    # TODO(zhwu): Add more types, e.g., cluster_lifecycle.


class UsageMessageToReport:
    """Message to be reported to Grafana Loki for each run"""

    def __init__(self) -> None:
        self.schema_version: str = usage_constants.USAGE_MESSAGE_SCHEMA_VERSION
        self.user: str = utils.get_logging_user_hash()
        self.run_id: str = utils.get_logging_run_id()
        self.time: str = str(time.time())
        self.cmd: str = common_utils.get_pretty_entry_point()
        self.entrypoint: Optional[str] = None
        self.cluster_names: List[str] = []
        self.new_cluster: bool = False
        #: Number of clusters in the cluster_names list.
        self.num_related_clusters: Optional[int] = None
        self.region: Optional[str] = None
        self.cluster_nodes: Optional[int] = None
        self.task_nodes: Optional[int] = None
        self.user_task_yaml: Optional[str] = None
        self.actual_task: Optional[Dict[str, Any]] = None
        self.ray_yamls: List[Dict[str, Any]] = []
        #: Number of Ray YAML files.
        self.num_tried_regions: Optional[int] = None
        self.runtimes: Dict[str, int] = {}
        self.stacktrace: Optional[str] = None

    def __repr__(self) -> str:
        d = self.__dict__.copy()
        return json.dumps(d)


usage_message = UsageMessageToReport()


def _make_labels_str(d):
    dict_str = ','.join(f'{k}="{v}"' for k, v in d.items())
    dict_str = '{' + dict_str + '}'
    return dict_str


def _send_message(message: str, message_type: MessageType):
    """Send the message to the Grafana Loki."""
    if env_options.Options.DISABLE_LOGGING.get():
        return

    log_timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat('T')

    prom_labels = {'type': message_type.value}

    headers = {'Content-type': 'application/json'}
    payload = {
        'streams': [{
            'labels': _make_labels_str(prom_labels),
            'entries': [{
                'ts': log_timestamp,
                'line': str(message),
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


def _clean_yaml(yaml_info: Dict[str, str]):
    """Remove sensitive information from user YAML."""
    cleaned_yaml_info = yaml_info.copy()
    for redact_type in ['setup', 'run', 'envs']:
        if redact_type in cleaned_yaml_info:
            contents = cleaned_yaml_info[redact_type]
            if not contents:
                cleaned_yaml_info[redact_type] = None
                continue
            lines = common_utils.dump_yaml_str({
                redact_type: contents
            }).strip().split('\n')
            cleaned_yaml_info[redact_type] = (
                f'{len(lines)} lines {redact_type.upper()}'
                ' redacted')

    return cleaned_yaml_info


def prepare_yaml(yaml_config_or_path: Union[Dict, str]):
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
    return yaml_info


def update_user_task_yaml(yaml_config_or_path: Union[Dict, str]):
    usage_message.user_task_yaml = prepare_yaml(yaml_config_or_path)


def update_actual_task(config: Dict[str, Any]):
    usage_message.actual_task = prepare_yaml(config)
    usage_message.task_nodes = config['num_nodes']


def update_ray_yaml(yaml_config_or_path: Union[Dict, str]):
    usage_message.ray_yamls.append(prepare_yaml(yaml_config_or_path))
    usage_message.num_tried_regions = len(usage_message.ray_yamls)


def update_cluster_name(cluster_name: Union[List[str], str]):
    if isinstance(cluster_name, str):
        usage_message.cluster_names = [cluster_name]
    else:
        usage_message.cluster_names = cluster_name
    usage_message.num_related_clusters = len(usage_message.cluster_names)


def update_region(region: str):
    usage_message.region = region


def update_cluster_nodes(num_nodes: int):
    usage_message.cluster_nodes = num_nodes


def set_new_cluster():
    usage_message.new_cluster = True


@contextlib.contextmanager
def update_runtime_context(name: str):
    try:
        start = time.time()
        yield
    finally:
        usage_message.runtimes[name] = time.time() - start


def update_runtime(name_or_fn: str):
    return common_utils.make_decorator(update_runtime_context, name_or_fn)


@contextlib.contextmanager
def entrypoint_context(name: str):
    is_outermost = usage_message.entrypoint is None
    if is_outermost:
        usage_message.entrypoint = name
    if env_options.Options.DISABLE_LOGGING.get() or not is_outermost:
        yield
        return

    try:
        yield
    except (Exception, SystemExit, KeyboardInterrupt):
        trace = traceback.format_exc()
        usage_message.stacktrace = trace
        raise
    finally:
        if is_outermost:
            try:
                _send_message(str(usage_message), MessageType.USAGE)
            except (Exception, SystemExit) as e:  # pylint: disable=broad-except
                logger.warning(
                    f'Usage logging exception caught: {type(e)}({e})')


def entrypoint(name_or_fn: str):
    return common_utils.make_decorator(entrypoint_context, name_or_fn)
