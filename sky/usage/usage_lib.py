"""Logging events to Grafana Loki."""

import contextlib
import datetime
import enum
import json
import os
import time
import traceback
import typing
from typing import Any, Callable, Dict, List, Optional, Union

import sky
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.usage import constants
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import inspect

    import requests

    from sky import resources as resources_lib
    from sky import task as task_lib
    from sky.utils import status_lib
else:
    # requests and inspect cost ~100ms to load, which can be postponed to
    # collection phase or skipped if user specifies no collection
    requests = adaptors_common.LazyImport('requests')
    inspect = adaptors_common.LazyImport('inspect')

logger = sky_logging.init_logger(__name__)


def _get_current_timestamp_ns() -> int:
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1e9)


class MessageType(enum.Enum):
    """Types for messages to be sent to Loki."""
    USAGE = 'usage'
    HEARTBEAT = 'heartbeat'
    # TODO(zhwu): Add more types, e.g., cluster_lifecycle.


class MessageToReport:
    """Abstract class for messages to be sent to Loki."""

    def __init__(self, schema_version: int):
        self.schema_version = schema_version
        self.start_time: Optional[int] = None
        self.send_time: Optional[int] = None

    def start(self):
        if self.start_time is None:
            self.start_time = _get_current_timestamp_ns()

    @property
    def message_sent(self):
        return self.send_time is not None or self.start_time is None

    def get_properties(self) -> Dict[str, Any]:
        properties = self.__dict__.copy()
        return {k: v for k, v in properties.items() if not k.startswith('_')}

    def __repr__(self) -> str:
        d = self.get_properties()
        return json.dumps(d)


class UsageMessageToReport(MessageToReport):
    """Message to be reported to Grafana Loki for each run"""

    def __init__(self) -> None:
        super().__init__(constants.USAGE_MESSAGE_SCHEMA_VERSION)
        # Message identifier.
        self.user: str = common_utils.get_user_hash()
        self.run_id: str = common_utils.get_usage_run_id()
        self.sky_version: str = sky.__version__
        self.sky_commit: str = sky.__commit__

        # Entry
        self.cmd: Optional[str] = common_utils.get_current_command()
        # The entrypoint on the client side.
        self.client_entrypoint: Optional[str] = None
        # The entrypoint on the server side, where each request has a entrypoint
        # and a single client_entrypoint can have multiple server-side
        # entrypoints.
        self.entrypoint: Optional[str] = None  # entrypoint_context
        #: Whether entrypoint is called by sky internal code.
        self.internal: bool = False  # set_internal

        # Basic info for the clusters.
        #: Clusters operated by the command.
        self.cluster_names: Optional[List[str]] = None  # update_cluster_name
        #: Number of clusters in the cluster_names list.
        self.num_related_clusters: Optional[int] = None  # update_cluster_name
        #: The final cloud of the cluster.
        self.cloud: Optional[str] = None  # update_cluster_resources
        #: The final region of the cluster.
        self.region: Optional[str] = None  # update_cluster_resources
        #: The final zone of the cluster.
        self.zone: Optional[str] = None  # update_cluster_resources
        #: The final instance_type of the cluster.
        self.instance_type: Optional[str] = None  # update_cluster_resources
        #: The final accelerators the cluster.
        self.accelerators: Optional[str] = None  # update_cluster_resources
        #: Number of accelerators per node.
        self.num_accelerators: Optional[int] = None  # update_cluster_resources
        #: Use spot
        self.use_spot: Optional[bool] = None  # update_cluster_resources
        #: Resources of the cluster.
        self.resources: Optional[Dict[str,
                                      Any]] = None  # update_cluster_resources
        #: Resources of the local cluster.
        self.local_resources: Optional[List[Dict[
            str, Any]]] = None  # update_local_cluster_resources
        #: The number of nodes in the cluster.
        self.num_nodes: Optional[int] = None  # update_cluster_resources
        #: The status of the cluster.
        self.original_cluster_status: Optional[
            str] = None  # update_cluster_status
        self._original_cluster_status_specified: Optional[
            bool] = False  # update_cluster_status
        self.final_cluster_status: Optional[
            str] = None  # update_final_cluster_status
        #: Whether the cluster is newly launched.
        self.is_new_cluster: bool = False  # set_new_cluster

        self.task_id: Optional[int] = None  # update_task_id
        # Task requested
        #: The number of nodes requested by the task.
        #: Requested cloud
        self.task_cloud: Optional[str] = None  # update_actual_task
        #: Requested region
        self.task_region: Optional[str] = None  # update_actual_task
        #: Requested zone
        self.task_zone: Optional[str] = None  # update_actual_task
        #: Requested instance_type
        self.task_instance_type: Optional[str] = None  # update_actual_task
        #: Requested accelerators
        self.task_accelerators: Optional[str] = None  # update_actual_task
        #: Requested number of accelerators per node
        self.task_num_accelerators: Optional[int] = None  # update_actual_task
        #: Requested use_spot
        self.task_use_spot: Optional[bool] = None  # update_actual_task
        #: Requested resources
        self.task_resources: Optional[Dict[str,
                                           Any]] = None  # update_actual_task
        #: Requested number of nodes
        self.task_num_nodes: Optional[int] = None  # update_actual_task
        # YAMLs converted to JSON.
        # TODO: include the skypilot config used in task yaml.
        self.user_task_yaml: Optional[List[Dict[
            str, Any]]] = None  # update_user_task_yaml
        self.actual_task: Optional[List[Dict[str,
                                             Any]]] = None  # update_actual_task
        self.ray_yamls: Optional[List[Dict[str, Any]]] = None
        #: Number of Ray YAML files.
        self.num_tried_regions: Optional[int] = None  # update_ray_yaml
        self.runtimes: Dict[str, float] = {}  # update_runtime
        self.exception: Optional[str] = None  # entrypoint_context
        self.stacktrace: Optional[str] = None  # entrypoint_context

        # Whether API server is deployed remotely.
        self.using_remote_api_server: bool = (
            common_utils.get_using_remote_api_server())

    def update_entrypoint(self, msg: str):
        if self.client_entrypoint is None:
            self.client_entrypoint = common_utils.get_current_client_entrypoint(
                msg)
        self.entrypoint = msg

    def set_internal(self):
        self.internal = True

    def update_user_task_yaml(self, yaml_config_or_path: Union[Dict, str]):
        self.user_task_yaml = prepare_json_from_yaml_config(yaml_config_or_path)

    def update_actual_task(self, task: 'task_lib.Task'):
        self.actual_task = prepare_json_from_yaml_config(task.to_yaml_config())
        self.task_num_nodes = task.num_nodes
        if task.resources:
            # resources is not None or empty.
            if len(task.resources) > 1:
                logger.debug('Multiple resources are specified in actual_task: '
                             f'{task.resources}.')
            resources = list(task.resources)[0]

            self.task_resources = resources.to_yaml_config()

            self.task_cloud = str(resources.cloud)
            self.task_region = resources.region
            self.task_zone = resources.zone
            self.task_instance_type = resources.instance_type
            self.task_use_spot = resources.use_spot
            # Update accelerators.
            if resources.accelerators:
                # Not None and not empty.
                if len(resources.accelerators) > 1:
                    logger.debug('Multiple accelerators are not supported: '
                                 f'{resources.accelerators}.')
                self.task_accelerators = list(resources.accelerators.keys())[0]
                self.task_num_accelerators = resources.accelerators[
                    self.task_accelerators]
            else:
                self.task_accelerators = None
                self.task_num_accelerators = None

    def update_task_id(self, task_id: int):
        self.task_id = task_id

    def update_ray_yaml(self, yaml_config_or_path: Union[Dict, str]):
        if self.ray_yamls is None:
            self.ray_yamls = []
        if self.num_tried_regions is None:
            self.num_tried_regions = 0
        # Only keep the latest ray yaml to reduce the size of the message.
        self.ray_yamls = prepare_json_from_yaml_config(yaml_config_or_path)
        self.num_tried_regions += 1

    def update_cluster_name(self, cluster_name: Union[List[str], str]):
        if isinstance(cluster_name, str):
            self.cluster_names = [cluster_name]
        else:
            self.cluster_names = cluster_name
        self.num_related_clusters = len(self.cluster_names)

    def update_cluster_resources(self, num_nodes: int,
                                 resources: 'resources_lib.Resources'):
        self.cloud = str(resources.cloud)
        self.region = resources.region
        self.zone = resources.zone
        self.instance_type = resources.instance_type
        self.use_spot = resources.use_spot

        # Update accelerators.
        if resources.accelerators:
            # Not None and not empty.
            if len(resources.accelerators) > 1:
                logger.debug('Multiple accelerators are not supported: '
                             f'{resources.accelerators}.')
            self.accelerators = list(resources.accelerators.keys())[0]
            self.num_accelerators = resources.accelerators[self.accelerators]
        else:
            self.accelerators = None
            self.num_accelerators = None

        self.num_nodes = num_nodes
        self.resources = resources.to_yaml_config()

    def update_local_cluster_resources(
            self, local_resources: List['resources_lib.Resources']):
        self.local_resources = [r.to_yaml_config() for r in local_resources]

    def update_cluster_status(
            self, original_status: Optional['status_lib.ClusterStatus']):
        status = original_status.value if original_status else None
        if not self._original_cluster_status_specified:
            self.original_cluster_status = status
            self._original_cluster_status_specified = True
        self.final_cluster_status = status

    def update_final_cluster_status(
            self, status: Optional['status_lib.ClusterStatus']):
        self.final_cluster_status = status.value if status is not None else None

    def set_new_cluster(self):
        self.is_new_cluster = True

    @contextlib.contextmanager
    def update_runtime_context(self, name: str):
        start = time.time()
        try:
            yield
        finally:
            self.runtimes[name] = time.time() - start

    def update_runtime(self, name_or_fn: str):
        return common_utils.make_decorator(self.update_runtime_context,
                                           name_or_fn)


class HeartbeatMessageToReport(MessageToReport):
    """Message to be reported to Grafana Loki for heartbeat on a cluster."""

    def __init__(self, interval_seconds: int = 600):
        super().__init__(constants.USAGE_MESSAGE_SCHEMA_VERSION)
        # This interval_seconds is mainly for recording the heartbeat interval
        # in the heartbeat message, so that the collector can use it.
        self.interval_seconds = interval_seconds

    def get_properties(self) -> Dict[str, Any]:
        properties = super().get_properties()
        # The run id is set by the skylet, which will always be the same for
        # the entire lifetime of the run.
        with open(os.path.expanduser(constants.USAGE_RUN_ID_FILE),
                  'r',
                  encoding='utf-8') as f:
            properties['run_id'] = f.read().strip()
        return properties


class MessageCollection:
    """A collection of messages."""

    def __init__(self):
        self._messages = {
            MessageType.USAGE: UsageMessageToReport(),
            MessageType.HEARTBEAT: HeartbeatMessageToReport()
        }

    @property
    def usage(self) -> UsageMessageToReport:
        return self._messages[MessageType.USAGE]

    @property
    def heartbeat(self) -> HeartbeatMessageToReport:
        return self._messages[MessageType.HEARTBEAT]

    def reset(self, message_type: MessageType):
        self._messages[message_type] = self._messages[message_type].__class__()

    def __getitem__(self, key):
        return self._messages[key]

    def items(self):
        return self._messages.items()

    def values(self):
        return self._messages.values()


messages = MessageCollection()


def _send_to_loki(message_type: MessageType):
    """Send the message to the Grafana Loki."""
    if env_options.Options.DISABLE_LOGGING.get():
        return

    message = messages[message_type]

    # In case the message has no start time, set it to the current time.
    message.start()
    message.send_time = _get_current_timestamp_ns()
    # Use send time instead of start time to avoid the message being dropped
    # by Loki, due to the timestamp being too old. We still have the start time
    # in the message for dashboard.
    log_timestamp = message.send_time

    environment = 'prod'
    if env_options.Options.IS_DEVELOPER.get():
        environment = 'dev'
    prom_labels = {
        'type': message_type.value,
        'environment': environment,
        'schema_version': message.schema_version,
    }
    if message_type == MessageType.USAGE:
        prom_labels['new_cluster'] = (message.original_cluster_status != 'UP'
                                      and message.final_cluster_status == 'UP')

    headers = {'Content-type': 'application/json'}
    payload = {
        'streams': [{
            'stream': prom_labels,
            'values': [[str(log_timestamp), str(message)]]
        }]
    }
    payload = json.dumps(payload)
    response = requests.post(constants.LOG_URL,
                             data=payload,
                             headers=headers,
                             timeout=0.5)
    if response.status_code != 204:
        logger.debug(
            f'Grafana Loki failed with response: {response.text}\n{payload}')
    messages.reset(message_type)


def _clean_yaml(yaml_info: Dict[str, Optional[str]]):
    """Remove sensitive information from user YAML."""
    cleaned_yaml_info = yaml_info.copy()
    for redact_type in constants.USAGE_MESSAGE_REDACT_KEYS:
        if redact_type in cleaned_yaml_info:
            contents = cleaned_yaml_info[redact_type]
            if not contents:
                cleaned_yaml_info[redact_type] = None
                continue

            message = None
            try:
                if callable(contents):
                    contents = inspect.getsource(contents)

                if type(contents) in constants.USAGE_MESSAGE_REDACT_TYPES:
                    lines = common_utils.dump_yaml_str({
                        redact_type: contents
                    }).strip().split('\n')
                    message = (f'{len(lines)} lines {redact_type.upper()}'
                               ' redacted')
                else:
                    message = (f'Error: Unexpected type for {redact_type}: '
                               f'{type(contents)}')
                    logger.debug(message)
            except Exception:  # pylint: disable=broad-except
                message = (
                    f'Error: Failed to dump lines for {redact_type.upper()}')
                logger.debug(message)

            cleaned_yaml_info[redact_type] = message

    return cleaned_yaml_info


def prepare_json_from_yaml_config(
        yaml_config_or_path: Union[Dict, str]) -> List[Dict[str, Any]]:
    """Upload safe contents of YAML file to Loki."""
    if isinstance(yaml_config_or_path, dict):
        yaml_info = [yaml_config_or_path]
        comment_lines = []
    else:
        with open(yaml_config_or_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            comment_lines = [line for line in lines if line.startswith('#')]
        yaml_info = common_utils.read_yaml_all(yaml_config_or_path)

    for i in range(len(yaml_info)):
        if yaml_info[i] is None:
            yaml_info[i] = {}
        yaml_info[i] = _clean_yaml(yaml_info[i])
        yaml_info[i]['__redacted_comment_lines'] = len(comment_lines)
    return yaml_info


def _send_local_messages():
    """Send all messages not been uploaded to Loki."""
    for msg_type, message in messages.items():
        if not message.message_sent and msg_type != MessageType.HEARTBEAT:
            # Avoid the fallback entrypoint to send the message again
            # in normal case.
            try:
                _send_to_loki(msg_type)
            except (Exception, SystemExit) as e:  # pylint: disable=broad-except
                logger.debug(f'Usage logging for {msg_type.value} '
                             f'exception caught: {type(e)}({e})')


def store_exception(e: Union[Exception, SystemExit, KeyboardInterrupt]) -> None:
    with ux_utils.enable_traceback():
        if hasattr(e, 'stacktrace') and e.stacktrace is not None:
            messages.usage.stacktrace = e.stacktrace
        else:
            trace = traceback.format_exc()
            messages.usage.stacktrace = trace
        if hasattr(e, 'detailed_reason') and e.detailed_reason is not None:
            messages.usage.stacktrace += '\nDetails: ' + e.detailed_reason
        messages.usage.exception = common_utils.remove_color(
            common_utils.format_exception(e))


def send_heartbeat(interval_seconds: int = 600):
    messages.heartbeat.interval_seconds = interval_seconds
    _send_to_loki(MessageType.HEARTBEAT)


def maybe_show_privacy_policy():
    """Show the privacy policy if it is not already shown."""
    # Show the policy message only when the entrypoint is used.
    # An indicator for PRIVACY_POLICY has already been shown.
    privacy_policy_indicator = os.path.expanduser(constants.PRIVACY_POLICY_PATH)
    if not env_options.Options.DISABLE_LOGGING.get():
        os.makedirs(os.path.dirname(privacy_policy_indicator), exist_ok=True)
        try:
            with open(privacy_policy_indicator, 'x', encoding='utf-8'):
                logger.info(constants.USAGE_POLICY_MESSAGE)
        except FileExistsError:
            pass


@contextlib.contextmanager
def entrypoint_context(name: str, fallback: bool = False):
    """Context manager for entrypoint.

    The context manager will send the usage message to Loki when exiting.
    The message will only be sent at the outermost level of the context.

    When the outermost context does not cover all the codepaths, an
    additional entrypoint_context with fallback=True can be used to wrap
    the global entrypoint to catch any exceptions that are not caught.
    """
    is_entry = messages.usage.entrypoint is None
    if is_entry and not fallback:
        for message in messages.values():
            message.start()
        messages.usage.update_entrypoint(name)
    if env_options.Options.DISABLE_LOGGING.get() or not is_entry:
        yield
        return

    # Should be the outermost entrypoint or the fallback entrypoint.
    try:
        yield
    except (Exception, SystemExit, KeyboardInterrupt) as e:
        store_exception(e)
        raise
    finally:
        if fallback:
            messages.usage.update_entrypoint(name)
        _send_local_messages()


T = typing.TypeVar('T')


@typing.overload
def entrypoint(
        name_or_fn: str,
        fallback: bool = False
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    ...


@typing.overload
def entrypoint(name_or_fn: Callable[..., T],
               fallback: bool = False) -> Callable[..., T]:
    ...


def entrypoint(
    name_or_fn: Union[str, Callable[..., T]],
    fallback: bool = False
) -> Union[Callable[..., T], Callable[[Callable[..., T]], Callable[..., T]]]:
    return common_utils.make_decorator(entrypoint_context,
                                       name_or_fn,
                                       fallback=fallback)


# Convenience methods below.


def record_cluster_name_for_current_operation(
        cluster_name: Union[List[str], str]) -> None:
    """Records cluster name(s) for the current operation.

    Usage:

       def op():  # CLI or programmatic API

           ...validate errors...

           usage_lib.record_cluster_name_for_current_operation(
              <actual clusters being operated on>)

           do_actual_op()
    """
    messages.usage.update_cluster_name(cluster_name)
