from typing import Dict

from ray import serve
from starlette import requests


@serve.deployment(route_prefix='/', num_replicas=2)
class ModelDeployment:

    def __init__(self, msg: str):
        self._msg = msg

    def __call__(self, request: requests.Request) -> Dict:
        del request  # unused
        return {'result': self._msg}


app = ModelDeployment.bind(msg='Hello Ray Serve!')
