import yaml

# from sky.backends import backend_utils
# from sky.utils import schemas


class SkyServiceSpec:

    def __init__(self, yaml_path: str):
        with open(yaml_path, 'r') as f:
            self.task = yaml.safe_load(f)
        if 'service' not in self.task:
            raise ValueError('Task YAML must have a "service" section')
        if 'port' not in self.task['service']:
            raise ValueError('Task YAML must have a "port" section')
        if 'readiness_probe' not in self.task['service']:
            raise ValueError('Task YAML must have a "readiness_probe" section')
        # TODO(tian): Enable schema when refactoring current code to accept new
        # version of service YAML.
        # service = self.task['service']
        # backend_utils.validate_schema(service, schemas.get_service_schema(),
        #                               'Invalid service YAML:')
        self._readiness_path = self.get_readiness_path()
        self._app_port = self.get_app_port()

    def get_readiness_path(self):
        # TODO: check if the path is valid
        return f':{self.task["service"]["port"]}{self.task["service"]["readiness_probe"]}'

    def get_app_port(self):
        # TODO: check if the port is valid
        return f'{self.task["service"]["port"]}'

    @property
    def readiness_path(self):
        return self._readiness_path

    @property
    def app_port(self):
        return self._app_port
