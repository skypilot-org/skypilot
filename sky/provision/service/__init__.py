import os
from typing import Any

import pydantic
import requests

from sky.provision import common
from sky.controller import config as client_config


def _request(method: str, api_name: str, service_path: str, payload: Any):
    if isinstance(payload, pydantic.BaseModel):
        payload = payload.dict()
    if method.lower() == 'get':
        params = payload
        payload = None
    else:
        params = None

    response = requests.request(
        method,
        f'{client_config.URL}/api/{api_name}/{service_path}',
        headers={'user-id': os.getenv('SKYPILOT_USER', '')},
        params=params,
        json=payload,
        **client_config.CLIENT_SSL_CONFIG)
    if not response.ok:
        raise RuntimeError(response.json())
    return response.json()


def _request_service_path(method: str, api_name: str, provider_name: str,
                          region: str, cluster_name: str, payload: Any):
    return _request(method, api_name,
                    f'{provider_name}/{region}/{cluster_name}', payload)


def bootstrap(provider_name: str, region: str, cluster_name: str,
              config: common.InstanceConfig) -> common.InstanceConfig:
    response = _request_service_path('post', bootstrap.__name__, provider_name,
                                     region, cluster_name, config)
    return common.InstanceConfig.parse_obj(response)


def start_instances(provider_name: str, region: str, cluster_name: str,
                    config: common.InstanceConfig) -> common.ProvisionMetadata:
    response = _request_service_path('post', start_instances.__name__,
                                     provider_name, region, cluster_name,
                                     config)
    return common.ProvisionMetadata.parse_obj(response)


def stop_instances(provider_name: str, region: str, cluster_name: str) -> None:
    _request_service_path('post', stop_instances.__name__, provider_name,
                          region, cluster_name, None)


def terminate_instances(provider_name: str, region: str,
                        cluster_name: str) -> None:
    _request_service_path('post', terminate_instances.__name__, provider_name,
                          region, cluster_name, None)


def stop_instances_with_self(provider_name: str) -> None:
    raise NotImplementedError


def terminate_instances_with_self(provider_name: str) -> None:
    raise NotImplementedError


def wait_instances(provider_name: str, region: str, cluster_name: str,
                   state: str) -> None:
    _request_service_path('get', wait_instances.__name__, provider_name, region,
                          cluster_name, {'state': state})


def get_cluster_metadata(provider_name: str, region: str,
                         cluster_name: str) -> common.ClusterMetadata:
    response = _request_service_path('get', get_cluster_metadata.__name__,
                                     provider_name, region, cluster_name, None)
    return common.ClusterMetadata.parse_obj(response)
