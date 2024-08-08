"""Handlers for the REST API return values."""
import base64
import pickle
import typing
from typing import Any, Dict, List

from sky import jobs as managed_jobs
from sky.clouds.service_catalog import common
from sky.data import storage
from sky.serve import serve_state
from sky.skylet import job_lib
from sky.utils import registry
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import clouds

handlers: Dict[str, Any] = {}


def decode_and_unpickle(obj: str) -> Any:
    return pickle.loads(base64.b64decode(obj.encode('utf-8')))


def register_handler(*names: str):
    """Decorator to register a handler."""

    def decorator(func):
        for name in names:
            handlers[name] = func
        return func

    return decorator


def get_handler(name: str):
    """Get the handler for name."""
    return handlers.get(name, handlers['default'])


@register_handler('default')
def default_decode_handler(return_value: Any) -> Any:
    """The default handler."""
    return return_value


@register_handler('status')
def decode_status(return_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clusters = return_value
    for cluster in clusters:
        cluster['handle'] = decode_and_unpickle(cluster['handle'])
        cluster['status'] = status_lib.ClusterStatus(cluster['status'])

    return clusters


@register_handler('launch', 'exec')
def decode_launch(return_value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'job_id': return_value['job_id'],
        'handle': decode_and_unpickle(return_value['handle']),
    }


@register_handler('start')
def decode_start(return_value: str) -> 'backends.CloudVmRayResourceHandle':
    return decode_and_unpickle(return_value)


@register_handler('queue')
def decode_queue(return_value: List[dict],) -> List[Dict[str, Any]]:
    jobs = return_value
    for job in jobs:
        job['status'] = job_lib.JobStatus(job['status'])
    return jobs


@register_handler('jobs/queue')
def decode_jobs_queue(return_value: List[dict],) -> List[Dict[str, Any]]:
    jobs = return_value
    for job in jobs:
        job['status'] = managed_jobs.ManagedJobStatus(job['status'])
    return jobs


@register_handler('serve/status')
def decode_serve_status(return_value: List[dict]) -> List[Dict[str, Any]]:
    service_statuses = return_value
    for service_status in service_statuses:
        service_status['status'] = serve_state.ServiceStatus(
            service_status['status'])
        for replica_info in service_status.get('replica_info', []):
            replica_info['status'] = serve_state.ReplicaStatus(
                replica_info['status'])
            replica_info['handle'] = decode_and_unpickle(replica_info['handle'])
    return service_statuses


@register_handler('cost_report')
def decode_cost_report(
        return_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for cluster_report in return_value:
        if cluster_report['status'] is not None:
            cluster_report['status'] = status_lib.ClusterStatus(
                cluster_report['status'])
        cluster_report['resources'] = decode_and_unpickle(
            cluster_report['resources'])
    return return_value


@register_handler('enabled_clouds')
def decode_enabled_clouds(return_value: List[str]) -> List['clouds.Cloud']:
    clouds = []
    for cloud_name in return_value:
        cloud = registry.CLOUD_REGISTRY.from_str(cloud_name)
        assert cloud is not None, return_value
        clouds.append(cloud)
    return clouds


@register_handler('list_accelerators')
def decode_list_accelerators(
    return_value: Dict[str, List[List[Any]]]
) -> Dict[str, List['common.InstanceTypeInfo']]:
    instance_dict: Dict[str, List['common.InstanceTypeInfo']] = {}
    for gpu, instance_type_infos in return_value.items():
        instance_dict[gpu] = []
        for instance_type_info in instance_type_infos:
            instance_dict[gpu].append(
                common.InstanceTypeInfo(*instance_type_info))
    return instance_dict


@register_handler('storage_ls')
def decode_storage_ls(
        return_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for storage_info in return_value:
        storage_info['status'] = status_lib.StorageStatus(
            storage_info['status'])
        storage_info['store'] = [
            storage.StoreType(store) for store in storage_info['store']
        ]
    return return_value
