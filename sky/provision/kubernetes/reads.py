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
import dataclasses
import json
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib import parse

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

# Outcomes of a Kubernetes API call, as passed to direct read observers.
OUTCOME_OK = 'ok'
OUTCOME_ERROR = 'error'


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
            order. Whether objects of built-in types carry apiVersion and
            kind depends on the source. The caller owns them.
        resource_version: The resourceVersion of the list or of the object;
            '' if get_object() found no object.
        source: SOURCE_CACHE or SOURCE_DIRECT.
        staleness_seconds: How long the cache's watch had been failing; 0
            for a direct read.
        reason: Why the read went to the API server; None when the cache
            served it.
    """
    items: List[Dict[str, Any]]
    resource_version: str
    source: str
    staleness_seconds: float
    reason: Optional[str]


class ResourceNotServedError(Exception):
    """The API server does not serve the resource type, e.g. a missing CRD."""


# (context, outcome, duration_seconds) of one Kubernetes API call.
DirectReadObserver = Callable[[str, str, float], None]

_DIRECT_READ_OBSERVERS: List[DirectReadObserver] = []


def register_direct_read_observer(observer: DirectReadObserver) -> None:
    """Register a function called after every API call a read makes.

    It is called in the reading thread, so it must be cheap and must not
    block. Idempotent.
    """
    if observer not in _DIRECT_READ_OBSERVERS:
        _DIRECT_READ_OBSERVERS.append(observer)


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
        context: Kubernetes context; None for the current context.
        resource: The resource type.
        namespace: Namespace to list; None for every namespace. Must be None
            for a cluster-scoped resource.
        label_selector: Label selector in Kubernetes syntax.
        field_selector: Field selector in Kubernetes syntax.
        consistency: Cached(max_age) or FRESH.

    Raises:
        exceptions.KubeAPIUnreachableError: The API server was needed and is
            known to be unreachable, or failed after retries.
        ResourceNotServedError: The API server does not serve the resource.
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
        context: Kubernetes context; None for the current context.
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
    context_name = (context if context is not None else
                    kubernetes_utils.get_current_kube_config_context_name())
    if isinstance(consistency, Fresh):
        reason = REASON_FRESH
    elif (context_name is None or
          not plugin_extensions.ClusterStateSource.is_registered()):
        reason = REASON_NO_SOURCE
    else:
        served = plugin_extensions.ClusterStateSource.get(
            plugin_extensions.ObjectQuery(
                context=context_name,
                group=resource.group,
                version=resource.version,
                resource=resource.plural,
                namespace=namespace,
                name=name,
                label_selector=label_selector,
                field_selector=field_selector,
                max_staleness_seconds=consistency.max_age))
        if isinstance(served, plugin_extensions.CachedObjects):
            metrics_utils.count_kube_read(context_name, resource.label,
                                          SOURCE_CACHE, '')
            metrics_utils.observe_kube_read_staleness(resource.label,
                                                      served.staleness_seconds)
            return ReadResult(items=served.items,
                              resource_version=served.resource_version,
                              source=SOURCE_CACHE,
                              staleness_seconds=served.staleness_seconds,
                              reason=None)
        if served is None:
            reason = REASON_NO_SOURCE
        else:
            reason = served.reason
            logger.debug(f'Reading {resource.label} of {context_name!r} from '
                         f'the API server: {served.reason} {served.message}')
    metrics_utils.count_kube_read(context_name or '', resource.label,
                                  SOURCE_DIRECT, reason)

    path = resource.path(namespace, name)
    query = tuple((key, value)
                  for key, value in (('labelSelector', label_selector),
                                     ('fieldSelector', field_selector))
                  if value)
    body = _IN_FLIGHT.do((context, path, query),
                         lambda: _get(context=context,
                                      context_name=context_name or '',
                                      resource_label=resource.label,
                                      path=path,
                                      query=query))
    if body is None:
        if name is None:
            raise ResourceNotServedError(
                f'Kubernetes context {context_name!r} does not serve '
                f'{resource.label} ({resource.group}/{resource.version}).')
        return ReadResult(items=[],
                          resource_version='',
                          source=SOURCE_DIRECT,
                          staleness_seconds=0,
                          reason=reason)
    parsed = json.loads(body)
    if name is not None:
        items = [parsed]
        resource_version = parsed['metadata'].get('resourceVersion', '')
    else:
        items = parsed.get('items') or []
        resource_version = (parsed.get('metadata') or
                            {}).get('resourceVersion', '')
    return ReadResult(items=items,
                      resource_version=resource_version,
                      source=SOURCE_DIRECT,
                      staleness_seconds=0,
                      reason=reason)


@kubernetes_utils._retry_on_error()  # pylint: disable=protected-access
def _get(*, context: Optional[str], context_name: str, resource_label: str,
         path: str, query: Tuple[Tuple[str, str], ...]) -> Optional[bytes]:
    """One GET of path; None if the API server answers 404."""
    start = time.monotonic()
    outcome = OUTCOME_ERROR
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
            body = response.data
        finally:
            response.release_conn()
        outcome = OUTCOME_OK
        return body
    except kubernetes.api_exception() as e:
        if e.status != 404:
            raise
        outcome = OUTCOME_OK
        return None
    finally:
        duration = time.monotonic() - start
        metrics_utils.observe_kube_read_api_call(resource_label, outcome,
                                                 duration)
        for observer in _DIRECT_READ_OBSERVERS:
            try:
                observer(context_name, outcome, duration)
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(f'Direct read observer failed: {e}')


class _InFlight:
    """Shares one call among the threads that make the same call at once.

    Every caller gets the same return value, so it must not be mutated;
    here it is the response body, which each caller parses for itself.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: Dict[Any, '_Call'] = {}

    def do(self, key: Any, fn: Callable[[], Any]) -> Any:
        with self._lock:
            call = self._calls.get(key)
            leader = call is None
            if call is None:
                call = _Call()
                self._calls[key] = call
        if not leader:
            call.done.wait()
            if call.error is not None:
                raise call.error
            return call.result
        try:
            call.result = fn()
            return call.result
        except BaseException as e:
            call.error = e
            raise
        finally:
            with self._lock:
                del self._calls[key]
            call.done.set()


class _Call:

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None


_IN_FLIGHT = _InFlight()
