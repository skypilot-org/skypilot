"""Reads of Kubernetes objects, served from a cache or the API server.

Read-only code reads Kubernetes objects with list_objects() and get_object().
A read with Cached(max_age) is served by the registered ClusterStateSource
when it can serve it within max_age, and by the API server otherwise. A read
with FRESH always goes to the API server.

Example:
    from sky.provision.kubernetes import reads

    result = reads.list_objects(context, reads.PODS, namespace='default',
                                label_selector='app=web')
    for pod in result.items:
        ...
"""
from concurrent import futures
import dataclasses
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib import parse

import ijson

from sky import exceptions
from sky import sky_logging
from sky.adaptors import kubernetes
from sky.metrics import utils as metrics_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import plugin_extensions

logger = sky_logging.init_logger(__name__)

SOURCE_CACHE = 'cache'
SOURCE_DIRECT = 'direct'

# Why a read went to the API server, besides the reasons in
# plugin_extensions.cluster_state_source.
REASON_FRESH = 'fresh'
REASON_NO_SOURCE = 'no_source'
# context=None, which the API client resolves in-cluster first; a source
# serves named contexts only.
REASON_UNNAMED_CONTEXT = 'unnamed_context'


@dataclasses.dataclass(frozen=True)
class Resource:
    """A Kubernetes resource type.

    Attributes:
        group: API group; '' for the core group.
        version: API version, e.g. 'v1'.
        plural: Plural resource name, e.g. 'pods'.
        namespaced: Whether objects of this type live in a namespace.
    """
    group: str
    version: str
    plural: str
    namespaced: bool

    @property
    def label(self) -> str:
        return f'{self.plural}.{self.group}' if self.group else self.plural

    def path(self, namespace: Optional[str], name: Optional[str]) -> str:
        if self.group:
            path = f'/apis/{self.group}/{self.version}'
        else:
            path = f'/api/{self.version}'
        if namespace is not None:
            path += f'/namespaces/{parse.quote(namespace, safe="")}'
        path += f'/{self.plural}'
        if name is not None:
            path += f'/{parse.quote(name, safe="")}'
        return path


NODES = Resource(group='', version='v1', plural='nodes', namespaced=False)
PODS = Resource(group='', version='v1', plural='pods', namespaced=True)


@dataclasses.dataclass(frozen=True)
class Cached:
    """Accept cached objects.

    Attributes:
        max_age: Seconds the cache's watch of the resource may have been
            failing. While the watch is healthy the cache follows the API
            server, so 0 still accepts a cache.
    """
    max_age: float = 60


@dataclasses.dataclass(frozen=True)
class Fresh:
    """Read the API server."""


FRESH = Fresh()

Consistency = Union[Cached, Fresh]


@dataclasses.dataclass
class ReadResult:
    """Objects returned by a read.

    Attributes:
        items: The objects in Kubernetes API JSON, in the API server's LIST
            order. They may be shared with other readers, so they must not
            be modified. Whether objects of built-in types carry apiVersion
            and kind depends on the source.
        resource_version: The resourceVersion of the list or of the object;
            '' if get_object() found no object.
        staleness_seconds: How long the cache's watch had been failing; 0
            for a direct read.
        reason: Why the read went to the API server; None when the cache
            served it.
    """
    items: List[Dict[str, Any]]
    resource_version: str
    staleness_seconds: float
    reason: Optional[str]

    @property
    def source(self) -> str:
        return SOURCE_CACHE if self.reason is None else SOURCE_DIRECT


def list_objects(
    context: Optional[str],
    resource: Resource,
    *,
    namespace: Optional[str] = None,
    label_selector: Optional[str] = None,
    field_selector: Optional[str] = None,
    consistency: Consistency = Cached()
) -> ReadResult:
    """List the objects of a resource type.

    Args:
        context: Kubernetes context. None reads the API server that the
            client picks by default (in-cluster config first, then the
            kubeconfig current context); only named contexts are served
            from a cache.
        resource: The resource type.
        namespace: Namespace to list; None for every namespace. Must be None
            for a cluster-scoped resource.
        label_selector: Label selector in Kubernetes syntax.
        field_selector: Field selector in Kubernetes syntax.
        consistency: Cached(max_age) or FRESH.

    Raises:
        exceptions.KubeAPIUnreachableError: The API server was needed and is
            known to be unreachable, or failed after retries.
        exceptions.KubernetesResourceNotServedError: The API server does not
            serve the resource.
    """
    return _read(context, resource, namespace, None, label_selector,
                 field_selector, consistency)


def get_object(
    context: Optional[str],
    resource: Resource,
    name: str,
    *,
    namespace: Optional[str] = None,
    consistency: Consistency = Cached()
) -> ReadResult:
    """Get one object; its items hold the object, or nothing if it is absent.

    Args:
        context: Kubernetes context. None reads the API server that the
            client picks by default (in-cluster config first, then the
            kubeconfig current context); only named contexts are served
            from a cache.
        resource: The resource type.
        name: Object name.
        namespace: The object's namespace. Required for a namespaced
            resource, and must be None for a cluster-scoped one.
        consistency: Cached(max_age) or FRESH.

    Raises:
        exceptions.KubeAPIUnreachableError: The API server was needed and is
            known to be unreachable, or failed after retries.
    """
    if resource.namespaced and namespace is None:
        raise ValueError(f'Getting a {resource.label} needs a namespace.')
    return _read(context, resource, namespace, name, None, None, consistency)


def _read(context: Optional[str], resource: Resource, namespace: Optional[str],
          name: Optional[str], label_selector: Optional[str],
          field_selector: Optional[str],
          consistency: Consistency) -> ReadResult:
    if namespace is not None and not resource.namespaced:
        raise ValueError(f'{resource.label} is cluster-scoped; got namespace '
                         f'{namespace!r}.')
    served = None
    if isinstance(consistency, Cached) and context is not None:
        object_query = plugin_extensions.ObjectQuery(
            context=context,
            group=resource.group,
            version=resource.version,
            resource=resource.plural,
            namespace=namespace,
            name=name,
            label_selector=label_selector,
            field_selector=field_selector,
            max_staleness_seconds=consistency.max_age)
        served = _IN_FLIGHT.do(
            object_query,
            lambda: plugin_extensions.ClusterStateSource.get(object_query))
    if isinstance(served, plugin_extensions.CachedObjects):
        metrics_utils.count_kube_read(context or '', resource.label,
                                      SOURCE_CACHE, '')
        metrics_utils.observe_kube_read_staleness(resource.label,
                                                  served.staleness_seconds)
        if name is None:
            resource_version = served.resource_version
        else:
            resource_version = _object_version(served.items)
        return ReadResult(items=served.items,
                          resource_version=resource_version,
                          staleness_seconds=served.staleness_seconds,
                          reason=None)
    if isinstance(served, plugin_extensions.Refusal):
        reason = served.reason
        logger.debug(f'Reading {resource.label} of {context!r} from the '
                     f'API server: {served.reason} {served.message}')
    elif isinstance(consistency, Fresh):
        reason = REASON_FRESH
    elif context is None:
        reason = REASON_UNNAMED_CONTEXT
    else:
        reason = REASON_NO_SOURCE
    metrics_utils.count_kube_read(context or '', resource.label, SOURCE_DIRECT,
                                  reason)

    path = resource.path(namespace, name)
    query = tuple((key, value)
                  for key, value in (('labelSelector', label_selector),
                                     ('fieldSelector', field_selector))
                  if value)
    # Each request may carry its own KUBECONFIG.
    kubeconfig = os.environ.get('KUBECONFIG')
    got = _IN_FLIGHT.do((context, kubeconfig, path, query),
                        lambda: _get(context=context,
                                     resource_label=resource.label,
                                     path=path,
                                     query=query,
                                     is_list=name is None))
    if got is None:
        if name is None:
            raise exceptions.KubernetesResourceNotServedError(
                f'Kubernetes context {context!r} does not serve '
                f'{resource.label} ({resource.group}/{resource.version}).')
        items, resource_version = [], ''
    else:
        items, resource_version = got
    return ReadResult(items=items,
                      resource_version=resource_version,
                      staleness_seconds=0,
                      reason=reason)


@kubernetes_utils._retry_on_error()  # pylint: disable=protected-access
def _get(*, context: Optional[str], resource_label: str, path: str,
         query: Tuple[Tuple[str, str], ...],
         is_list: bool) -> Optional[Tuple[List[Dict[str, Any]], str]]:
    """One GET of path: (items, resourceVersion), or None on 404."""
    start = time.monotonic()
    outcome = 'error'
    try:
        response = kubernetes.api_client(context).call_api(
            path,
            'GET',
            query_params=list(query),
            header_params={'Accept': 'application/json'},
            auth_settings=['BearerToken'],
            _return_http_data_only=True,
            _preload_content=False,
            _request_timeout=kubernetes.API_TIMEOUT)
        try:
            if is_list:
                got = _parse_list(response)
            else:
                obj = json.loads(response.data)
                got = [obj], _object_version([obj])
        except (kubernetes.urllib3.exceptions.HTTPError, ijson.JSONError,
                json.JSONDecodeError) as e:
            # A body cut off mid-read is retried like a request that failed.
            raise kubernetes.max_retry_error()(None, path, reason=e) from e
        finally:
            response.release_conn()
        outcome = 'ok'
        return got
    except kubernetes.api_exception() as e:
        if e.status != 404:
            raise
        outcome = 'ok'
        return None
    finally:
        metrics_utils.observe_kube_read_api_call(resource_label, outcome,
                                                 time.monotonic() - start)


def _object_version(items: List[Dict[str, Any]]) -> str:
    """The resourceVersion of the one object a get returned, or ''."""
    if not items:
        return ''
    return items[0].get('metadata', {}).get('resourceVersion', '')


def _parse_list(response: Any) -> Tuple[List[Dict[str, Any]], str]:
    items: List[Dict[str, Any]] = []
    resource_version = ''
    for key, value in ijson.kvitems(
            response,
            '',
            use_float=True,
            buf_size=kubernetes_utils.IJSON_BUFFER_SIZE):
        if key == 'items':
            items = value or []
        elif key == 'metadata':
            resource_version = value.get('resourceVersion', '')
    return items, resource_version


class _InFlight:
    """Shares one call among the threads that make the same call at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: Dict[Any, futures.Future] = {}

    def do(self, key: Any, fn: Callable[[], Any]) -> Any:
        with self._lock:
            call = self._calls.get(key)
            leader = call is None
            if leader:
                call = self._calls[key] = futures.Future()
        assert call is not None
        if not leader:
            return call.result()
        try:
            result = fn()
            call.set_result(result)
            return result
        except BaseException as e:
            call.set_exception(e)
            raise
        finally:
            with self._lock:
                del self._calls[key]


_IN_FLIGHT = _InFlight()
