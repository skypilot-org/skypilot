from typing import Optional, Dict

from sky.backends import backend_utils
from sky.utils import schemas
from sky.utils import ux_utils


class SkyServiceSpec:

    def __init__(
        self,
        readiness_path: str,
        readiness_timeout: int,
        app_port: int,
        min_replica: int,
        max_replica: Optional[int] = None,
        qpm_upper_threshold: Optional[int] = None,
        qpm_lower_threshold: Optional[int] = None,
    ):
        if max_replica is not None and max_replica < min_replica:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'max_replica must be greater than or equal to min_replica')
        # TODO: check if the path is valid
        self._readiness_path = f':{app_port}{readiness_path}'
        self._readiness_timeout = readiness_timeout
        # TODO: check if the port is valid
        self._app_port = str(app_port)
        self._min_replica = min_replica
        self._max_replica = max_replica
        self._qpm_upper_threshold = qpm_upper_threshold
        self._qpm_lower_threshold = qpm_lower_threshold

    @classmethod
    def from_yaml_config(cls, config: Optional[Dict[str, str]]):
        if config is None:
            return None

        backend_utils.validate_schema(config, schemas.get_service_schema(),
                                      'Invalid service YAML:')

        service_config = {}
        service_config['readiness_path'] = config['readiness_probe']['path']
        service_config['readiness_timeout'] = config['readiness_probe'][
            'readiness_timeout']
        service_config['app_port'] = config['port']
        service_config['min_replica'] = config['replica_policy']['min_replica']
        service_config['max_replica'] = config['replica_policy'].get(
            'max_replica', None)
        service_config['qpm_upper_threshold'] = config['replica_policy'].get(
            'qpm_upper_threshold', None)
        service_config['qpm_lower_threshold'] = config['replica_policy'].get(
            'qpm_lower_threshold', None)

        return SkyServiceSpec(**service_config)

    @property
    def readiness_path(self):
        return self._readiness_path

    @property
    def readiness_timeout(self):
        return self._readiness_timeout

    @property
    def app_port(self):
        return self._app_port

    @property
    def min_replica(self):
        return self._min_replica

    @property
    def max_replica(self):
        return self._max_replica

    @property
    def qpm_upper_threshold(self):
        return self._qpm_upper_threshold

    @property
    def qpm_lower_threshold(self):
        return self._qpm_lower_threshold
