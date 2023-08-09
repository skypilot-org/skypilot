"""Service specification for SkyServe."""
import os
import yaml
from typing import Optional, Dict, Any

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
        min_replica: int,
        max_replica: Optional[int] = None,
        qps_upper_threshold: Optional[float] = None,
        qps_lower_threshold: Optional[float] = None,
        post_data: Optional[Dict[str, Any]] = None,
    ):
        if max_replica is not None and max_replica < min_replica:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'max_replica must be greater than or equal to min_replica')
        if app_port == constants.CONTROL_PLANE_PORT:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'App port cannot be {constants.CONTROL_PLANE_PORT} '
                    'since it is reserved for the control plane. '
                    ' Please use a different port.')
        # TODO: check if the path is valid
        self._readiness_path = f':{app_port}{readiness_path}'
        self._initial_delay_seconds = initial_delay_seconds
        # TODO: check if the port is valid
        self._app_port = str(app_port)
        self._min_replica = min_replica
        self._max_replica = max_replica
        self._qps_upper_threshold = qps_upper_threshold
        self._qps_lower_threshold = qps_lower_threshold
        self._post_data = post_data

    @staticmethod
    def from_yaml_config(config: Optional[Dict[str, Any]]):
        if config is None:
            return None

        backend_utils.validate_schema(config, schemas.get_service_schema(),
                                      'Invalid service YAML:')

        service_config = {}
        service_config['readiness_path'] = config['readiness_probe']['path']
        service_config['initial_delay_seconds'] = config['readiness_probe'][
            'initial_delay_seconds']
        service_config['app_port'] = config['port']
        service_config['min_replica'] = config['replica_policy']['min_replica']
        service_config['max_replica'] = config['replica_policy'].get(
            'max_replica', None)
        service_config['qps_upper_threshold'] = config['replica_policy'].get(
            'qps_upper_threshold', None)
        service_config['qps_lower_threshold'] = config['replica_policy'].get(
            'qps_lower_threshold', None)
        service_config['post_data'] = config['readiness_probe'].get(
            'post_data', None)

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
        add_if_not_none('readiness_probe', 'path',
                        self.readiness_path[len(f':{self.app_port}'):])
        add_if_not_none('readiness_probe', 'initial_delay_seconds',
                        self.initial_delay_seconds)
        add_if_not_none('readiness_probe', 'post_data', self.post_data)
        add_if_not_none('replica_policy', 'min_replica', self.min_replica)
        add_if_not_none('replica_policy', 'max_replica', self.max_replica)
        add_if_not_none('replica_policy', 'qps_upper_threshold',
                        self.qps_upper_threshold)
        add_if_not_none('replica_policy', 'qps_lower_threshold',
                        self.qps_lower_threshold)

        return config

    def policy_str(self):
        if self.max_replica == self.min_replica or self.max_replica is None:
            plural = ''
            if self.min_replica > 1:
                plural = 'S'
            return f'#REPLICA{plural}: {self.min_replica}'
        # TODO(tian): Refactor to contain more information
        return f'AUTOSCALE [{self.min_replica}, {self.max_replica}]'

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
    def min_replica(self) -> int:
        return self._min_replica

    @property
    def max_replica(self) -> Optional[int]:
        return self._max_replica

    @property
    def qps_upper_threshold(self) -> Optional[float]:
        return self._qps_upper_threshold

    @property
    def qps_lower_threshold(self) -> Optional[float]:
        return self._qps_lower_threshold

    @property
    def post_data(self) -> Optional[Dict[str, Any]]:
        return self._post_data
