"""Service specification for SkyServe."""
import json
import os
import textwrap
from typing import Any, Dict, Optional

import yaml

from sky.backends import backend_utils
from sky.serve import constants
from sky.utils import schemas
from sky.utils import ux_utils


class SkyServiceSpec:
    """SkyServe service specification."""

    def __init__(
        self,
        readiness_path: str,
        initial_delay_seconds: int,
        app_port: int,
        min_replicas: int,
        max_replicas: Optional[int] = None,
        qps_upper_threshold: Optional[float] = None,
        qps_lower_threshold: Optional[float] = None,
        post_data: Optional[Dict[str, Any]] = None,
        controller_resources: Optional[Dict[str, Any]] = None,
    ):
        if min_replicas < 0:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'min_replicas must be greater than or equal to 0')
        if max_replicas is not None and max_replicas < min_replicas:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'max_replicas must be greater than or equal to min_replicas'
                )
        if app_port == constants.CONTROLLER_PORT:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'App port cannot be {constants.CONTROLLER_PORT} '
                    'since it is reserved for the controller. '
                    'Please use a different port.')
        if not readiness_path.startswith('/'):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('readiness_path must start with a slash (/). '
                                 f'Got: {readiness_path}')
        self._readiness_path = readiness_path
        self._initial_delay_seconds = initial_delay_seconds
        if app_port < 0 or app_port > 65535:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Invalid app port: {app_port}. '
                    'Please use a port number between 0 and 65535.')
        self._app_port = str(app_port)
        self._min_replicas = min_replicas
        self._max_replicas = max_replicas
        self._qps_upper_threshold = qps_upper_threshold
        self._qps_lower_threshold = qps_lower_threshold
        self._post_data = post_data
        self._controller_resources = controller_resources

    @staticmethod
    def from_yaml_config(config: Optional[Dict[str, Any]]):
        if config is None:
            return None

        backend_utils.validate_schema(config, schemas.get_service_schema(),
                                      'Invalid service YAML:')
        if 'replicas' in config and 'replica_policy' in config:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cannot specify both `replicas` and `replica_policy` in '
                    'the service YAML. Please use one of them.')

        service_config = {}
        service_config['app_port'] = config['port']

        readiness_section = config['readiness_probe']
        if isinstance(readiness_section, str):
            service_config['readiness_path'] = readiness_section
            initial_delay_seconds = None
            post_data = None
        else:
            service_config['readiness_path'] = readiness_section['path']
            initial_delay_seconds = readiness_section.get(
                'initial_delay_seconds', None)
            post_data = readiness_section.get('post_data', None)
        if initial_delay_seconds is None:
            ids = constants.DEFAULT_INITIAL_DELAY_SECONDS
            initial_delay_seconds = ids
        service_config['initial_delay_seconds'] = initial_delay_seconds
        if isinstance(post_data, str):
            try:
                post_data = json.loads(post_data)
            except json.JSONDecodeError as e:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Invalid JSON string for `post_data` in the '
                        '`readiness_probe` section of your service YAML.'
                    ) from e
        service_config['post_data'] = post_data

        policy_section = config.get('replica_policy', None)
        simplified_policy_section = config.get('replicas', None)
        if policy_section is None or simplified_policy_section is not None:
            if simplified_policy_section is not None:
                min_replicas = simplified_policy_section
            else:
                min_replicas = constants.DEFAULT_MIN_REPLICAS
            service_config['min_replicas'] = min_replicas
            service_config['max_replicas'] = None
            service_config['qps_upper_threshold'] = None
            service_config['qps_lower_threshold'] = None
        else:
            service_config['min_replicas'] = policy_section['min_replicas']
            service_config['max_replicas'] = policy_section.get(
                'max_replicas', None)
            service_config['qps_upper_threshold'] = policy_section.get(
                'qps_upper_threshold', None)
            service_config['qps_lower_threshold'] = policy_section.get(
                'qps_lower_threshold', None)

        service_config['controller_resources'] = config.pop(
            'controller_resources', None)

        return SkyServiceSpec(**service_config)

    @staticmethod
    def from_yaml(yaml_path: str):
        with open(os.path.expanduser(yaml_path), 'r') as f:
            config = yaml.safe_load(f)

        if isinstance(config, str):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('YAML loaded as str, not as dict. '
                                 f'Is it correct? Path: {yaml_path}')

        if config is None:
            config = {}

        if 'service' not in config:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Service YAML must have a "service" section. '
                                 f'Is it correct? Path: {yaml_path}')

        return SkyServiceSpec.from_yaml_config(config['service'])

    def to_yaml_config(self):
        config = dict()

        def add_if_not_none(section, key, value, no_empty: bool = False):
            if no_empty and not value:
                return
            if value is not None:
                if key is None:
                    config[section] = value
                else:
                    if section not in config:
                        config[section] = dict()
                    config[section][key] = value

        add_if_not_none('port', None, int(self.app_port))
        add_if_not_none('readiness_probe', 'path', self.readiness_path)
        add_if_not_none('readiness_probe', 'initial_delay_seconds',
                        self.initial_delay_seconds)
        add_if_not_none('readiness_probe', 'post_data', self.post_data)
        add_if_not_none('replica_policy', 'min_replicas', self.min_replicas)
        add_if_not_none('replica_policy', 'max_replicas', self.max_replicas)
        add_if_not_none('replica_policy', 'qps_upper_threshold',
                        self.qps_upper_threshold)
        add_if_not_none('replica_policy', 'qps_lower_threshold',
                        self.qps_lower_threshold)
        add_if_not_none('controller_resources', None,
                        self._controller_resources)

        return config

    def probe_str(self):
        if self.post_data is None:
            return f'GET {self.readiness_path}'
        return f'POST {self.readiness_path} {json.dumps(self.post_data)}'

    def policy_str(self):
        min_plural = '' if self.min_replicas == 1 else 's'
        if self.max_replicas == self.min_replicas or self.max_replicas is None:
            return f'Fixed {self.min_replicas} replica{min_plural}'
        # TODO(tian): Refactor to contain more information
        max_plural = '' if self.max_replicas == 1 else 's'
        return (f'Autoscaling from {self.min_replicas} to '
                f'{self.max_replicas} replica{max_plural}')

    def __repr__(self) -> str:
        return textwrap.dedent(f"""\
            Readiness probe method:        {self.probe_str()}
            Replica autoscaling policy:    {self.policy_str()}
            Service initial delay seconds: {self.initial_delay_seconds}

            Please refer to SkyPilot Serve document for detailed explanations.
        """)

    @property
    def readiness_suffix(self) -> str:
        return f':{self._app_port}{self._readiness_path}'

    @property
    def readiness_path(self) -> str:
        return self._readiness_path

    @property
    def initial_delay_seconds(self) -> int:
        return self._initial_delay_seconds

    @property
    def app_port(self) -> str:
        return self._app_port

    @property
    def min_replicas(self) -> int:
        return self._min_replicas

    @property
    def max_replicas(self) -> Optional[int]:
        return self._max_replicas

    @property
    def qps_upper_threshold(self) -> Optional[float]:
        return self._qps_upper_threshold

    @property
    def qps_lower_threshold(self) -> Optional[float]:
        return self._qps_lower_threshold

    @property
    def post_data(self) -> Optional[Dict[str, Any]]:
        return self._post_data

    @property
    def controller_resources(self) -> Optional[Dict[str, Any]]:
        return self._controller_resources
