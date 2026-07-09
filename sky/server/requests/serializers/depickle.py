"""Prototype: pickle-free codecs for the requests protocol.

[DEPICKLE PROTOTYPE] Name/JSON-based encoding for request rows
(entrypoint, request_body, error) to evaluate removing pickle from the
API server scheduling layer, so that a non-Python scheduler can produce
and consume request rows.

Design:
- entrypoint: encoded as ``fn:<module>:<qualname>`` (resolved via import
  on decode) or ``daemon:<daemon_id>`` for internal daemon bound methods.
- request_body: encoded as a JSON object ``{"cls": "<module>:<qualname>",
  "data": "<model_dump_json>"}`` (pydantic v2 round-trip).
- error: the ``exceptions.serialize_exception`` dict is stored as JSON
  directly when JSON-safe.

Every spot where JSON encoding is impossible falls back to pickle
(prefixed with ``pkl:``) and records a pitfall entry to
``~/.sky/depickle_pitfalls.jsonl`` — running a server against real
traffic therefore produces a complete inventory of the gaps that a Go
scheduler would have to close.

Legacy raw-base64-pickle values (rows written by unmodified code) are
still decodable for a smooth in-place switch.
"""
import base64
import importlib
import pathlib
import pickle
import threading
import time
from typing import Any, Callable, Dict, Optional

import orjson

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

_FN_PREFIX = 'fn:'
_DAEMON_PREFIX = 'daemon:'
_PKL_PREFIX = 'pkl:'

_PITFALL_LOG = pathlib.Path('~/.sky/depickle_pitfalls.jsonl').expanduser()
_pitfall_lock = threading.Lock()


def log_pitfall(kind: str, detail: str, **extra: Any) -> None:
    """Record a place where JSON encoding was not possible."""
    logger.warning(f'[DEPICKLE-PITFALL] {kind}: {detail}')
    entry: Dict[str, Any] = {
        'ts': time.time(),
        'kind': kind,
        'detail': detail,
    }
    entry.update(extra)
    try:
        with _pitfall_lock:
            _PITFALL_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _PITFALL_LOG.open('a', encoding='utf-8') as f:
                f.write(orjson.dumps(entry).decode('utf-8') + '\n')
    except OSError:
        pass


def _pickle_b64(obj: Any) -> str:
    return base64.b64encode(pickle.dumps(obj)).decode('utf-8')


def _unpickle_b64(data: str) -> Any:
    return pickle.loads(base64.b64decode(data.encode('utf-8')))


def _resolve_ref(ref: str) -> Any:
    """Resolve a ``<module>:<qualname>`` reference via import."""
    module_name, _, qualname = ref.partition(':')
    obj: Any = importlib.import_module(module_name)
    for part in qualname.split('.'):
        obj = getattr(obj, part)
    return obj


# === Entrypoint ===


def encode_entrypoint(func: Callable) -> str:
    """Encode a request entrypoint by name instead of pickling it."""
    self_obj = getattr(func, '__self__', None)
    if self_obj is not None:
        # Bound method. The only expected case is
        # InternalRequestDaemon.run_event; encode via the daemon registry.
        daemon_id = getattr(self_obj, 'id', None)
        if daemon_id is not None and getattr(func, '__name__',
                                             '') == 'run_event':
            return _DAEMON_PREFIX + daemon_id
        log_pitfall('entrypoint-bound-method', repr(func))
        return _PKL_PREFIX + _pickle_b64(func)
    module = getattr(func, '__module__', None)
    qualname = getattr(func, '__qualname__', None)
    if module is None or qualname is None or '<' in qualname:
        # Lambdas / nested functions are not importable by name.
        log_pitfall('entrypoint-not-importable', f'{module}:{qualname}')
        return _PKL_PREFIX + _pickle_b64(func)
    ref = f'{module}:{qualname}'
    try:
        resolved = _resolve_ref(ref)
    except (ImportError, AttributeError) as e:
        log_pitfall('entrypoint-not-resolvable', f'{ref}: {e!r}')
        return _PKL_PREFIX + _pickle_b64(func)
    if resolved is not func:
        # E.g. decorated function whose __qualname__ points at the
        # wrapper's original. Still encode by name (pickle-by-reference
        # has the same behavior), but record it for analysis.
        log_pitfall('entrypoint-identity-mismatch', ref)
    return _FN_PREFIX + ref


def decode_entrypoint(encoded: str) -> Callable:
    """Decode an entrypoint encoded by ``encode_entrypoint``.

    Raises AttributeError/ImportError on unresolvable references, same as
    unpickling does, so existing fallbacks keep working.
    """
    if encoded.startswith(_FN_PREFIX):
        return _resolve_ref(encoded[len(_FN_PREFIX):])
    if encoded.startswith(_DAEMON_PREFIX):
        # Lazy import to avoid a module cycle with sky.server.daemons.
        # pylint: disable=import-outside-toplevel
        from sky.server import daemons
        daemon_id = encoded[len(_DAEMON_PREFIX):]
        for daemon in daemons.INTERNAL_REQUEST_DAEMONS:
            if daemon.id == daemon_id:
                return daemon.run_event
        raise AttributeError(f'Unknown internal daemon id: {daemon_id}')
    if encoded.startswith(_PKL_PREFIX):
        return _unpickle_b64(encoded[len(_PKL_PREFIX):])
    # Legacy raw base64 pickle (row written by unmodified code).
    return _unpickle_b64(encoded)


# === Request body ===


def encode_request_body(body: Any) -> str:
    """Encode a pydantic request body as JSON instead of pickling it."""
    cls = type(body)
    cls_ref = f'{cls.__module__}:{cls.__qualname__}'
    try:
        data = body.model_dump_json()
    except Exception as e:  # pylint: disable=broad-except
        log_pitfall('body-dump-failed', f'{cls_ref}: {e!r}')
        return _PKL_PREFIX + _pickle_b64(body)
    # Prototype-only round-trip verification: catches fields that
    # serialize but do not deserialize back to an equal model (lossy
    # unions, custom classes, etc.). Remove for production.
    try:
        restored = cls.model_validate_json(data)
    except Exception as e:  # pylint: disable=broad-except
        log_pitfall('body-validate-failed', f'{cls_ref}: {e!r}')
        return _PKL_PREFIX + _pickle_b64(body)
    if restored != body:
        diffs = _model_diff(body, restored)
        log_pitfall('body-lossy-roundtrip', f'{cls_ref}: {diffs}')
        return _PKL_PREFIX + _pickle_b64(body)
    return orjson.dumps({'cls': cls_ref, 'data': data}).decode('utf-8')


def _model_diff(a: Any, b: Any) -> str:
    try:
        da, db = dict(a), dict(b)
        fields = [
            k for k in da
            if type(da.get(k)) is not type(db.get(k)) or da.get(k) != db.get(k)
        ]
        return ','.join(fields) or '<unknown>'
    except Exception:  # pylint: disable=broad-except
        return '<diff-failed>'


def decode_request_body(encoded: str) -> Any:
    """Decode a request body encoded by ``encode_request_body``."""
    if encoded.startswith(_PKL_PREFIX):
        return _unpickle_b64(encoded[len(_PKL_PREFIX):])
    if encoded.startswith('{'):
        wrapper = orjson.loads(encoded)
        cls = _resolve_ref(wrapper['cls'])
        return cls.model_validate_json(wrapper['data'])
    # Legacy raw base64 pickle.
    return _unpickle_b64(encoded)


# === Error ===


def encode_error(serialized_exception: Any, type_name: str,
                 message: str) -> Dict[str, Any]:
    """Build the request ``error`` dict, JSON-first.

    ``serialized_exception`` is the dict produced by
    ``exceptions.serialize_exception`` ({type, args, attributes,
    stacktrace}); it is JSON-safe unless the exception carries non-JSON
    attributes (e.g. a ResourceHandle).
    """
    try:
        # Probe JSON-safety; store the dict inline on success.
        orjson.dumps(serialized_exception)
        return {
            'object_json': serialized_exception,
            'type': type_name,
            'message': message,
        }
    except TypeError as e:
        log_pitfall('error-not-json', f'{type_name}: {e!r}', message=message)
        return {
            'object': _pickle_b64(serialized_exception),
            'type': type_name,
            'message': message,
        }


def decode_error_object(error: Dict[str, Any]) -> Optional[Any]:
    """Extract the serialized-exception dict from an ``error`` dict.

    Handles both the JSON form ('object_json') and the legacy/fallback
    pickled form ('object').
    """
    if 'object_json' in error:
        obj = error['object_json']
        # orjson serializes tuples as lists; deserialize_exception passes
        # args positionally so a list works, but normalize for safety.
        if isinstance(obj, dict) and isinstance(obj.get('args'), list):
            obj = dict(obj)
            obj['args'] = tuple(obj['args'])
        return obj
    return _unpickle_b64(error['object'])
