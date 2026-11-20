"""API server usage by caller: calls, execution time and result bytes.

Each process records at most MAX_CALLER_NAMES distinct (caller, name) pairs
and MAX_CLIENTS distinct client label sets; anything beyond is recorded under
the caller 'other', so the series count stays bounded however many users and
request names a deployment has. The bound is per process: the series exported
from all of a server's processes, including exited ones, can exceed it.
"""
import contextlib
import contextvars
import re
import threading
import time
from typing import Iterable, Iterator, NamedTuple, Optional, Set, Tuple

from sky import models
from sky.metrics import utils as metrics_utils
from sky.server import middleware_utils
from sky.skylet import constants

MAX_CALLER_NAMES = 512
MAX_CLIENTS = 256

OTHER = 'other'
NONE = 'none'

KIND_SYNC = 'sync'
KIND_HANDLER = 'handler'


class Caller(NamedTuple):
    id: str
    name: str
    type: str


ANONYMOUS = Caller('anonymous', 'anonymous', 'anonymous')
_OVERFLOW = Caller(OTHER, OTHER, OTHER)


def caller_of(user_id: Optional[str], user_name: Optional[str]) -> Caller:
    if not user_id:
        return ANONYMOUS
    if user_id == constants.SKYPILOT_SYSTEM_USER_ID:
        # The request env carries the server process's own user name.
        return Caller(user_id, user_id, 'system')
    if models.User(id=user_id).is_service_account():
        caller_type = 'service_account'
    else:
        caller_type = 'user'
    return Caller(user_id, user_name or user_id, caller_type)


def caller_of_user(user: Optional[models.User]) -> Caller:
    if user is None:
        return ANONYMOUS
    return caller_of(user.id, user.name)


# The account behind an impersonated HTTP call; usage is attributed to it.
_impersonator: contextvars.ContextVar[Optional[models.User]] = (
    contextvars.ContextVar('caller_usage_impersonator', default=None))


def set_impersonator(user: models.User) -> None:
    _impersonator.set(user)


def impersonator() -> Optional[models.User]:
    return _impersonator.get()


class _Admitted:
    """Label keys this process records as is, up to a cap."""

    def __init__(self, cap: int):
        self._cap = cap
        self._keys: Set[Tuple[str, ...]] = set()
        self._lock = threading.Lock()

    def admit(self, key: Tuple[str, ...]) -> bool:
        if key in self._keys:
            return True
        with self._lock:
            if key in self._keys:
                return True
            if len(self._keys) >= self._cap:
                return False
            self._keys.add(key)
            return True


_caller_names = _Admitted(MAX_CALLER_NAMES)
_clients = _Admitted(MAX_CLIENTS)


def _labels(caller: Caller, name: str) -> dict:
    if not _caller_names.admit((*caller, name)):
        caller = _OVERFLOW
    return {
        'caller': caller.name,
        'caller_id': caller.id,
        'caller_type': caller.type,
        'name': name,
    }


def _record_execution(caller: Caller, name: str, kind: str,
                      seconds: float) -> None:
    labels = _labels(caller, name)
    metrics_utils.SKY_APISERVER_CALLER_CALLS_TOTAL.labels(kind=kind,
                                                          **labels).inc()
    metrics_utils.SKY_APISERVER_CALLER_EXECUTION_SECONDS_TOTAL.labels(
        kind=kind, **labels).inc(max(seconds, 0.0))


def record_execution(caller: Caller, name: str, kind: str,
                     seconds: float) -> None:
    if metrics_utils.METRICS_ENABLED:
        middleware_utils.record_safely('caller execution', _record_execution,
                                       caller, name, kind, seconds)


@contextlib.contextmanager
def executing(caller: Caller, name: str, kind: str) -> Iterator[None]:
    """Records one call and its wall time, however the block exits."""
    start = time.monotonic()
    try:
        yield
    finally:
        record_execution(caller, name, kind, time.monotonic() - start)


def _record_bytes(counter, caller: Caller, name: str, num_bytes: int) -> None:
    counter.labels(**_labels(caller, name)).inc(num_bytes)


def record_stored_bytes(caller: Caller, name: str,
                        num_bytes: Optional[int]) -> None:
    if metrics_utils.METRICS_ENABLED and num_bytes:
        middleware_utils.record_safely(
            'caller stored bytes', _record_bytes,
            metrics_utils.SKY_APISERVER_CALLER_STORED_RESULT_BYTES_TOTAL,
            caller, name, num_bytes)


def record_returned_bytes(caller: Caller, name: str,
                          num_bytes: Optional[int]) -> None:
    if metrics_utils.METRICS_ENABLED and num_bytes:
        middleware_utils.record_safely(
            'caller returned bytes', _record_bytes,
            metrics_utils.SKY_APISERVER_CALLER_RETURNED_RESULT_BYTES_TOTAL,
            caller, name, num_bytes)


# Ordered: the first matching User-Agent prefix (lowercased) wins.
_UA_FAMILIES = (
    ('mozilla/', 'browser'),
    ('curl/', 'curl'),
    ('wget/', 'wget'),
    ('python-requests/', 'python-requests'),
    ('python-httpx/', 'python-httpx'),
    ('python-urllib', 'python-urllib'),
    ('python/', 'python-aiohttp'),
    ('aiohttp/', 'python-aiohttp'),
    ('go-http-client/', 'go'),
    ('node', 'node'),
    ('undici', 'node'),
    ('axios/', 'node'),
    ('java', 'java'),
    ('okhttp', 'java'),
    ('kube-probe/', 'kube-probe'),
)

_CLIENT_TYPE_RE = re.compile(r'[a-z0-9._-]{1,32}')


def ua_family(user_agent: Optional[str]) -> str:
    if not user_agent:
        return NONE
    ua = user_agent.strip().lower()
    for prefix, family in _UA_FAMILIES:
        if ua.startswith(prefix):
            return family
    return OTHER


def client_type(header: Optional[str]) -> str:
    if not header:
        return NONE
    value = header.strip().lower()
    return value if _CLIENT_TYPE_RE.fullmatch(value) else OTHER


def api_version(header: Optional[str]) -> str:
    if not header:
        return NONE
    value = header.strip()
    return value if value.isdigit() and len(value) <= 4 else 'invalid'


def _record_client(caller: Caller, client: str, family: str,
                   version: str) -> None:
    if not _clients.admit((*caller, client, family, version)):
        caller, client, family, version = _OVERFLOW, OTHER, OTHER, OTHER
    metrics_utils.SKY_APISERVER_CALLER_CLIENT_CALLS_TOTAL.labels(
        caller=caller.name,
        caller_id=caller.id,
        caller_type=caller.type,
        client_type=client,
        ua_family=family,
        api_version=version).inc()


def record_client(caller: Caller, client_type_header: Optional[str],
                  user_agent: Optional[str],
                  api_version_header: Optional[str]) -> None:
    if metrics_utils.METRICS_ENABLED:
        middleware_utils.record_safely('caller client', _record_client, caller,
                                       client_type(client_type_header),
                                       ua_family(user_agent),
                                       api_version(api_version_header))


class HttpCall:
    """Per HTTP call state the route sets for the metrics middleware."""
    __slots__ = ('scheduled_request',)

    def __init__(self) -> None:
        # The route scheduled a request; its execution is recorded by the
        # executor, not as a route handler call.
        self.scheduled_request = False


_http_call: contextvars.ContextVar[Optional[HttpCall]] = (
    contextvars.ContextVar('caller_usage_http_call', default=None))


def begin_http_call() -> Tuple[HttpCall, contextvars.Token]:
    call = HttpCall()
    return call, _http_call.set(call)


def end_http_call(token: contextvars.Token) -> None:
    _http_call.reset(token)


def mark_request_scheduled() -> None:
    call = _http_call.get()
    if call is not None:
        call.scheduled_request = True


def route_template(
    path: str, route_path_format: str,
    spanning_values: Iterable[str] = ()) -> str:
    """The full route path of a request that matched route_path_format.

    FastAPI 0.16x matches an included router's own route, whose path_format
    lacks the prefix given to include_router; the prefix is the leading
    segments of the request path that the route's path does not cover.
    spanning_values are the request's values of `{name:path}` parameters,
    which path_format writes as one segment.
    """
    segments = route_path_format.count('/') + sum(
        value.count('/') for value in spanning_values)
    return path.rsplit('/', segments)[0] + route_path_format
