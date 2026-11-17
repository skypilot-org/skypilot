"""Request results kept outside the requests table.

When ``store_enabled()``, a result whose encoded form exceeds
``MAX_IN_ROW_BYTES`` is compressed into a file under the blob storage's request
results directory, and the request row keeps a small reference in its
``return_value`` column instead. Stored results are always read back.
"""
import dataclasses
import os
import pathlib
from typing import Any, Optional
import uuid
import zlib

import orjson

from sky import sky_logging
from sky.metrics import utils as metrics_lib
from sky.server.blob import blob_storage as bs
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

# Encoded results larger than this are stored outside the requests table.
MAX_IN_ROW_BYTES = 1024 * 1024

_REFERENCE_KEY = '__skypilot_stored_result__'
_SUFFIX = '.json.zlib'
_TMP_SUFFIX = '.tmp'
_COMPRESSION_LEVEL = 1


@dataclasses.dataclass(frozen=True)
class StoredResult:
    """Reference to a request result stored outside the requests table."""
    # Size of the encoded result in bytes.
    size: int


def results_dir() -> Optional[pathlib.Path]:
    return bs.get_blob_storage().request_results_dir()


def store_enabled() -> bool:
    return (os.environ.get(constants.ENV_VAR_SERVER_STORE_LARGE_RESULTS) == '1'
            and results_dir() is not None)


def result_path(request_id: str) -> pathlib.Path:
    directory = results_dir()
    if directory is None:
        raise RuntimeError('The blob storage keeps no request results.')
    return directory / f'{request_id}{_SUFFIX}'


def store(request_id: str, request_name: str, encoded: str) -> StoredResult:
    """Compress an encoded result into the results directory."""
    data = encoded.encode('utf-8')
    compressed = zlib.compress(data, _COMPRESSION_LEVEL)
    path = result_path(request_id)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    tmp_path = directory / f'{request_id}.{uuid.uuid4().hex}{_TMP_SUFFIX}'
    try:
        tmp_path.write_bytes(compressed)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    if metrics_lib.METRICS_ENABLED:
        metrics_lib.SKY_APISERVER_STORED_RESULTS_TOTAL.labels(
            name=request_name).inc()
        metrics_lib.SKY_APISERVER_STORED_RESULT_BYTES_TOTAL.labels(
            name=request_name, form='encoded').inc(len(data))
        metrics_lib.SKY_APISERVER_STORED_RESULT_BYTES_TOTAL.labels(
            name=request_name, form='compressed').inc(len(compressed))
    return StoredResult(size=len(data))


def _load_encoded(request_id: str) -> bytes:
    try:
        compressed = result_path(request_id).read_bytes()
    except FileNotFoundError as e:
        raise RuntimeError(
            f'The result of request {request_id} is no longer available.'
        ) from e
    return zlib.decompress(compressed)


def load(request_id: str) -> Any:
    """Load a stored result as the decoded JSON value of the encoded result."""
    return orjson.loads(_load_encoded(request_id))


def load_text(request_id: str) -> str:
    """Load a stored result as the encoded text it was stored from."""
    return _load_encoded(request_id).decode('utf-8')


def to_column(reference: StoredResult) -> str:
    """The ``return_value`` column value that refers to a stored result."""
    return orjson.dumps({
        _REFERENCE_KEY: dataclasses.asdict(reference)
    }).decode('utf-8')


def from_column(value: Any) -> Any:
    """Turn a decoded ``return_value`` column into a StoredResult if it is a
    reference; return any other value unchanged."""
    if isinstance(value, dict) and len(value) == 1 and _REFERENCE_KEY in value:
        return StoredResult(**value[_REFERENCE_KEY])
    return value


def request_id_of(file_name: str) -> Optional[str]:
    """The request a file in the results directory belongs to."""
    # Both stored results and interrupted writes are named '<request_id>.<...>'.
    return file_name.split('.', 1)[0] or None
