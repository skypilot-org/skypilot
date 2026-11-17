"""Request results kept outside the requests table.

A result whose encoded form exceeds ``MAX_IN_ROW_BYTES`` is compressed into
a file under the blob storage's request results directory, and the request
row keeps a small reference in its ``return_value`` column instead.
"""
import dataclasses
import os
import pathlib
import stat
from typing import Any, List, Tuple
import uuid
import zlib

import orjson

from sky import sky_logging
from sky.metrics import utils as metrics_lib
from sky.server.blob import blob_storage as bs

logger = sky_logging.init_logger(__name__)

# Encoded results larger than this are stored outside the requests table.
MAX_IN_ROW_BYTES = 1024 * 1024

_REFERENCE_KEY = '__skypilot_stored_result__'
_SUFFIX = '.json.zlib'
_TMP_SUFFIX = '.tmp'
# Level 1 compresses encoded results about 10x at several hundred MB/s.
_COMPRESSION_LEVEL = 1


@dataclasses.dataclass(frozen=True)
class StoredResult:
    """Reference to a request result stored outside the requests table."""
    # Size of the encoded result in bytes.
    size: int


def results_dir() -> pathlib.Path:
    return bs.get_blob_storage().request_results_dir()


def result_path(request_id: str) -> pathlib.Path:
    return results_dir() / f'{request_id}{_SUFFIX}'


def store(request_id: str, request_name: str, encoded: str) -> StoredResult:
    """Compress an encoded result into the results directory."""
    data = encoded.encode('utf-8')
    compressed = zlib.compress(data, _COMPRESSION_LEVEL)
    directory = results_dir()
    directory.mkdir(parents=True, exist_ok=True)
    tmp_path = directory / f'{request_id}.{uuid.uuid4().hex}{_TMP_SUFFIX}'
    try:
        tmp_path.write_bytes(compressed)
        os.replace(tmp_path, result_path(request_id))
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


def load(request_id: str) -> Any:
    """Load a stored result as the decoded JSON value of the encoded result."""
    try:
        compressed = result_path(request_id).read_bytes()
    except FileNotFoundError as e:
        raise RuntimeError(
            f'The result of request {request_id} is no longer available.'
        ) from e
    return orjson.loads(zlib.decompress(compressed))


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


def list_stale_files(cutoff: float) -> List[Tuple[str, str, int]]:
    """List (request_id, path, size) of files written before cutoff."""
    directory = results_dir()
    if not directory.is_dir():
        return []
    stale = []
    with os.scandir(directory) as entries:
        for entry in entries:
            # Both stored results and interrupted writes are named
            # '<request_id>.<...>'.
            request_id = entry.name.split('.', 1)[0]
            if not request_id:
                continue
            try:
                st = os.stat(entry.path, follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_mtime >= cutoff:
                continue
            stale.append((request_id, entry.path, st.st_size))
    return stale
