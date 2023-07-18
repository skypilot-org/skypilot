import yaml

from sky.backends import backend_utils
from sky.utils import schemas


class SkyServiceSpec:

    def __init__(self, yaml_path: str):
        with open(yaml_path, 'r') as f:
            task = yaml.safe_load(f)
        if 'service' not in self.task:
            raise ValueError('Task YAML must have a "service" section')
        self.service = task['service']
        backend_utils.validate_schema(self.service, schemas.get_service_schema(),
                                      'Invalid service YAML:')
        # TODO: check if the path is valid
        self._readiness_path = f':{self.service["port"]}{self.service["readiness_probe"]["path"]}'
        self._readiness_timeout = self.service['readiness_probe']['timeout']
        # TODO: check if the port is valid
        self._app_port = str(self.service["port"])
        self._min_replica = self.service['replica_policy']['min_replica']
        self._max_replica = self.service['replica_policy'].get('max_replica', None)
        self._qpm_upper_threshold = self.service['replica_policy'].get('qpm_upper_threshold', None)
        self._qpm_lower_threshold = self.service['replica_policy'].get('qpm_lower_threshold', None)

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
