"""Common utilities for the client."""

import contextlib
import dataclasses
import hashlib
import json
import logging
import math
import os
import pathlib
import tempfile
import time
import typing
from typing import Dict, Generator, Iterable, Optional, Tuple
import uuid
import zipfile

import filelock

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.client import service_account_auth
from sky.data import data_utils
from sky.data import storage_utils
from sky.schemas.api import responses as api_responses
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server import versions
from sky.server.requests import payloads
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import httpx
    import requests

    import sky
    from sky import dag as dag_lib
else:
    httpx = adaptors_common.LazyImport('httpx')
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)

# The chunk size for downloading the logs from the API server.
_DOWNLOAD_CHUNK_BYTES = 8192
# The chunk size for the zip file to be uploaded to the API server. We split
# the zip file into chunks to avoid network issues for large request body that
# can be caused by NGINX's client_max_body_size or Cloudflare's upload limit.
# As of 09/25/2025, the upload limit for Cloudflare's free plan is 100MiB:
# https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-413/
_UPLOAD_CHUNK_BYTES = 100 * 1024 * 1024

FILE_UPLOAD_LOGS_DIR = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                    'file_uploads')
_FILE_UPLOAD_LOCK_DIR = '~/.sky/locks/file_uploads'

# Connection timeout when sending requests to the API server.
API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS = 5


def download_logs_from_api_server(
        paths_on_api_server: Iterable[str],
        remote_machine_prefix: str = str(
            server_common.api_server_user_logs_dir_prefix()),
        local_machine_prefix: str = constants.SKY_LOGS_DIRECTORY
) -> Dict[str, str]:
    """Downloads the logs from the API server.

    Args:
        paths_on_api_server: The paths on the API server to download.
        remote_machine_prefix: The prefix of the remote machine to save the
        logs.
        local_machine_prefix: The prefix of the local machine to save the logs.

    Returns:
        A dictionary mapping the remote path on API server to the local path.
    """
    remote2local_path_dict = {
        remote_path: remote_path.replace(
            # TODO(zhwu): handling the replacement locally is not stable, and
            # may cause issues when we change the pattern of the remote path.
            # This should be moved to remote API server. A proper way might be
            # set the returned path to be started with a special prefix, instead
            # of using the `api_server_user_logs_dir_prefix()`.
            remote_machine_prefix,
            local_machine_prefix) for remote_path in paths_on_api_server
    }
    # Check if any local log directories already exist before downloading
    for local_path in remote2local_path_dict.values():
        expanded_path = os.path.expanduser(local_path)
        if os.path.exists(expanded_path):
            logger.warning(
                f'Log directory {local_path} already exists. '
                f'This may overwrite logs from a previous cluster with the '
                f'same name and job ID.')
    body = payloads.DownloadBody(folder_paths=list(paths_on_api_server),)
    response = server_common.make_authenticated_request(
        'POST',
        '/download',
        json=json.loads(body.model_dump_json()),
        stream=True)
    if response.status_code == 200:
        remote_home_path = response.headers.get('X-Home-Path')
        assert remote_home_path is not None, response.headers
        with tempfile.NamedTemporaryFile(prefix='skypilot-logs-download-',
                                         delete=True) as temp_file:
            # Download the zip file from the API server to the local machine.
            for chunk in response.iter_content(
                    chunk_size=_DOWNLOAD_CHUNK_BYTES):
                temp_file.write(chunk)
            temp_file.flush()

            # Unzip the downloaded file and save the logs to the correct local
            # directory.
            with zipfile.ZipFile(temp_file, 'r') as zipf:
                for member in zipf.namelist():
                    # Determine the new path
                    zipped_filename = os.path.basename(member)
                    zipped_dir = os.path.dirname('/' + member)
                    local_dir = zipped_dir.replace(remote_home_path, '~')
                    for remote_path, local_path in remote2local_path_dict.items(
                    ):
                        if local_dir.startswith(remote_path):
                            local_dir = local_dir.replace(
                                remote_path, local_path)
                            break
                    else:
                        raise ValueError(f'Invalid folder path: {zipped_dir}')
                    new_path = pathlib.Path(
                        local_dir).expanduser().resolve() / zipped_filename
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    if member.endswith('/'):
                        # If it is a directory, we need to create it.
                        new_path.mkdir(parents=True, exist_ok=True)
                    else:
                        with zipf.open(member) as member_file:
                            new_path.write_bytes(member_file.read())

        return remote2local_path_dict
    else:
        raise Exception(
            f'Failed to download logs: {response.status_code} {response.text}')


# === Upload files to API server ===


class FileChunkIterator:
    """A file-like object that reads from a file in chunks."""

    def __init__(self, file_obj, chunk_size: int, chunk_index: int):
        self.file_obj = file_obj
        self.chunk_size = chunk_size
        self.chunk_index = chunk_index
        self.bytes_read = 0

    def __iter__(self):
        # Seek to the correct position for this chunk
        self.file_obj.seek(self.chunk_index * self.chunk_size)
        while self.bytes_read < self.chunk_size:
            # Read a smaller buffer size to keep memory usage low
            buffer_size = min(64 * 1024,
                              self.chunk_size - self.bytes_read)  # 64KB buffer
            data = self.file_obj.read(buffer_size)
            if not data:
                break
            self.bytes_read += len(data)
            yield data


@dataclasses.dataclass
class UploadChunkParams:
    """Parameters for uploading a single chunk of a zip file."""
    client: 'httpx.Client'
    upload_id: str
    chunk_index: int
    total_chunks: int
    file_path: str
    upload_logger: logging.Logger
    log_file: str
    # For backward compatibility
    # TODO(aylei): remove this and always use /upload_v2 after 0.14.0
    endpoint: str = '/upload'


def _upload_chunk_with_retry(params: UploadChunkParams) -> str:
    """Uploads a chunk of a zip file to the API server.

    Returns:
        Status of the upload.
    """
    upload_logger = params.upload_logger
    upload_logger.info(
        f'Uploading chunk: {params.chunk_index + 1} / {params.total_chunks}')

    server_url = server_common.get_server_url()
    max_attempts = 3
    sa_headers = service_account_auth.get_service_account_headers()
    with open(params.file_path, 'rb') as f:
        for attempt in range(max_attempts):
            response = params.client.post(
                f'{server_url}{params.endpoint}',
                params={
                    'user_hash': common_utils.get_user_hash(),
                    'upload_id': params.upload_id,
                    'chunk_index': str(params.chunk_index),
                    'total_chunks': str(params.total_chunks),
                },
                content=FileChunkIterator(f, _UPLOAD_CHUNK_BYTES,
                                          params.chunk_index),
                headers={
                    'Content-Type': 'application/octet-stream',
                    **sa_headers,
                },
                cookies=server_common.get_api_cookie_jar())
            if response.status_code == 200:
                data = response.json()
                status = data.get('status')
                msg = ('Uploaded chunk: '
                       f'{params.chunk_index + 1} / {params.total_chunks} '
                       f'(Status: {status})')
                if status == api_responses.UploadStatus.UPLOADING.value:
                    missing_chunks = data.get('missing_chunks')
                    if missing_chunks:
                        msg += f' - Waiting for chunks: {missing_chunks}'
                upload_logger.info(msg)
                return status
            elif attempt < max_attempts - 1:
                upload_logger.error(
                    f'Failed to upload chunk: '
                    f'{params.chunk_index + 1} / {params.total_chunks}: '
                    f'{response.content.decode("utf-8")}')
                upload_logger.info(
                    f'Retrying... ({attempt + 1} / {max_attempts})')
                if response.status_code == 503:
                    # If the server is temporarily unavailable,
                    # wait a little longer before retrying.
                    time.sleep(10)
                else:
                    time.sleep(1)
            else:
                try:
                    response_details = response.json().get('detail')
                except Exception:  # pylint: disable=broad-except
                    response_details = response.content
                error_msg = (
                    f'Failed to upload chunk: {params.chunk_index + 1} / '
                    f'{params.total_chunks}: {response_details} '
                    f'(Status code: {response.status_code})')
                upload_logger.error(error_msg)
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        ux_utils.error_message(error_msg + '\n',
                                               params.log_file,
                                               is_local=True))
    # If we reach here, the upload failed.
    return 'failed'


@contextlib.contextmanager
def _setup_upload_logger(
        log_file: str) -> Generator[logging.Logger, None, None]:
    try:
        upload_logger = logging.getLogger('sky.upload')
        upload_logger.propagate = False
        handler = logging.FileHandler(os.path.expanduser(log_file),
                                      encoding='utf-8')
        handler.setFormatter(sky_logging.FORMATTER)
        upload_logger.addHandler(handler)
        upload_logger.setLevel(logging.DEBUG)
        yield upload_logger
    finally:
        upload_logger.removeHandler(handler)
        handler.close()


_HASH_CHUNK_SIZE = 2**18


def _compute_zip_blob_id(zip_path: str) -> str:
    """Compute a stable content hash from a zip file.

    Iterates over zip entries in sorted order and hashes
    (filename, content) pairs. Ignores zip metadata (timestamps, OS).

    Compared to common_utils.hash_file, this hash is stable across re-zips.
    """
    entries: list = []
    with zipfile.ZipFile(zip_path, 'r') as zipf:
        for info in zipf.infolist():
            name = info.filename
            is_symlink = (info.external_attr >> 28) == 0xA
            if name.endswith('/') and not is_symlink:
                # Directory entry
                entries.append((name, hashlib.sha256(b'').digest()))
            else:
                # File or symlink (symlink content is the target path)
                eh = hashlib.sha256()
                with zipf.open(info) as f:
                    while True:
                        chunk = f.read(_HASH_CHUNK_SIZE)
                        if not chunk:
                            break
                        eh.update(chunk)
                entries.append((name, eh.digest()))

    entries.sort(key=lambda e: e[0])
    h = hashlib.sha256()
    for name, digest in entries:
        h.update(name.encode('utf-8'))
        h.update(digest)
    return h.hexdigest()


def upload_mounts_to_api_server(
    dag: 'sky.Dag',
    workdir_only: bool = False,
) -> Tuple['dag_lib.Dag', Optional[str]]:
    """Upload user files to remote API server.

    This function needs to be called after sdk.validate(),
    as the file paths need to be expanded to keep file_mounts_mapping
    aligned with the actual task uploaded to SkyPilot API server.

    We don't use FastAPI's built-in multipart upload, as nginx's
    client_max_body_size can block the request due to large request body, i.e.,
    even though the multipart upload streams the file to the server, there is
    only one HTTP request, and a large request body will be blocked by nginx.

    Args:
        dag: The dag where the file mounts are defined.
        workdir_only: Whether to only upload the workdir, which is used for
            `exec`, as it does not need other files/folders in file_mounts.

    Returns:
        A tuple of (dag, file_mounts_blob_id). The dag has file_mounts_mapping
        updated. file_mounts_blob_id is the blob ID of file mounts if /upload_v2
        was used, or None if the old /upload path was used.
    """

    if server_common.is_api_server_local():
        return dag, None

    def _full_path(src: str) -> str:
        return os.path.abspath(os.path.expanduser(src))

    upload_list = []
    for task_ in dag.tasks:
        task_.file_mounts_mapping = {}
        if task_.workdir and isinstance(task_.workdir, str):
            workdir = task_.workdir
            assert os.path.isabs(workdir)
            upload_list.append(workdir)
            task_.file_mounts_mapping[workdir] = workdir
        if workdir_only:
            continue
        if task_.file_mounts is not None:
            for src in task_.file_mounts.values():
                if not data_utils.is_cloud_store_url(src):
                    assert os.path.isabs(src)
                    upload_list.append(src)
                    task_.file_mounts_mapping[src] = src
                if src == constants.LOCAL_SKYPILOT_CONFIG_PATH_PLACEHOLDER:
                    # The placeholder for the local skypilot config path is in
                    # file mounts for controllers. It will be replaced with the
                    # real path for config file on API server.
                    pass
        if task_.storage_mounts is not None:
            for storage in task_.storage_mounts.values():
                storage_source = storage.source
                is_cloud_store_url = (
                    isinstance(storage_source, str) and
                    data_utils.is_cloud_store_url(storage_source))
                if (storage_source is not None and not is_cloud_store_url):
                    if isinstance(storage_source, str):
                        storage_source = [storage_source]
                    for src in storage_source:
                        upload_list.append(_full_path(src))
                        task_.file_mounts_mapping[src] = _full_path(src)
        if (task_.service is not None and
                task_.service.tls_credential is not None):
            keyfile = task_.service.tls_credential.keyfile
            certfile = task_.service.tls_credential.certfile
            upload_list.append(_full_path(keyfile))
            upload_list.append(_full_path(certfile))
            task_.file_mounts_mapping[keyfile] = _full_path(keyfile)
            task_.file_mounts_mapping[certfile] = _full_path(certfile)

    if upload_list:
        os.makedirs(os.path.expanduser(FILE_UPLOAD_LOGS_DIR), exist_ok=True)
        upload_id = sky_logging.get_run_timestamp()
        upload_id = f'{upload_id}-{uuid.uuid4().hex[:8]}'
        log_file = os.path.join(FILE_UPLOAD_LOGS_DIR, f'{upload_id}.log')

        # Check if the server supports v2 upload API.
        remote_api_version = versions.get_remote_api_version()
        use_v2 = (remote_api_version is not None and
                  remote_api_version >= server_constants.UPLOAD_API_V2_VERSION)

        def _upload_zip(endpoint: str, upload_id: str, zip_file_path: str,
                        status: rich_utils.GeneralStatus,
                        upload_logger: logging.Logger):
            zip_file_size = os.path.getsize(zip_file_path)
            total_chunks = int(math.ceil(zip_file_size / _UPLOAD_CHUNK_BYTES))
            timeout = httpx.Timeout(None, read=180.0)
            status.update(
                ux_utils.spinner_message(
                    'Uploading files to API server (2/2 - Uploading)',
                    log_file,
                    is_local=True))

            upload_completed = False
            with httpx.Client(timeout=timeout) as client:
                total_retries = 3
                for retry in range(total_retries):
                    chunk_params = [
                        UploadChunkParams(client,
                                          upload_id,
                                          chunk_index,
                                          total_chunks,
                                          zip_file_path,
                                          upload_logger,
                                          log_file,
                                          endpoint=endpoint)
                        for chunk_index in range(total_chunks)
                    ]
                    statuses = subprocess_utils.run_in_parallel(
                        _upload_chunk_with_retry, chunk_params)
                    if any(status == api_responses.UploadStatus.COMPLETED.value
                           for status in statuses):
                        upload_completed = True
                        break
                    else:
                        upload_logger.info(
                            f'No chunk upload returned completed status. '
                            'Retrying entire upload... '
                            f'({retry + 1} / {total_retries})')
            if not upload_completed:
                raise RuntimeError('Failed to upload files to API server.')

        blob_id = None
        logger.info(ux_utils.starting_message('Uploading files to API server'))
        with rich_utils.client_status(
                ux_utils.spinner_message(
                    'Uploading files to API server (1/2 - Zipping)',
                    log_file,
                    is_local=True)) as status, _setup_upload_logger(
                        log_file) as upload_logger:
            with tempfile.NamedTemporaryFile(suffix='.zip',
                                             delete=False) as temp_zip_file:
                upload_logger.info(
                    f'Zipping files to be uploaded: {upload_list}')
                storage_utils.zip_files_and_folders(upload_list,
                                                    temp_zip_file.name)
                upload_logger.info(f'Zipped files to: {temp_zip_file.name}')

            if use_v2:
                blob_id = _compute_zip_blob_id(temp_zip_file.name)
                upload_logger.info(f'Computed blob ID: {blob_id}')
                lock_dir = os.path.expanduser(_FILE_UPLOAD_LOCK_DIR)
                os.makedirs(lock_dir, exist_ok=True)
                # In v2, lock on blob_id to avoid concurrent uploads of the same
                # blob.
                with filelock.FileLock(os.path.join(lock_dir,
                                                    f'{blob_id}.lock')):
                    # Check existence and skip upload if already present.
                    resp = server_common.make_authenticated_request(
                        'GET',
                        '/upload_v2/blob',
                        params={
                            'user_hash': common_utils.get_user_hash(),
                            'blob_id': blob_id,
                        })
                    if resp.status_code != 200:
                        raise RuntimeError(f'Failed to check blob existence: '
                                           f'{resp.status_code} '
                                           f'{resp.content.decode("utf-8")}')
                    if resp.json().get('exists'):
                        upload_logger.info('Blob already exists, skipping')
                        os.unlink(temp_zip_file.name)
                        logger.info(
                            ux_utils.finishing_message('Files uploaded',
                                                       log_file,
                                                       is_local=True))
                        return dag, blob_id
                    # In v2, we use the blob_id as the upload id to share
                    # the uploaded blob across requests.
                    _upload_zip('/upload_v2', blob_id, temp_zip_file.name,
                                status, upload_logger)
            else:
                _upload_zip('/upload', upload_id, temp_zip_file.name, status,
                            upload_logger)

            os.unlink(temp_zip_file.name)
            upload_logger.info(f'Uploaded files: {upload_list}')
        logger.info(
            ux_utils.finishing_message('Files uploaded',
                                       log_file,
                                       is_local=True))
        if use_v2:
            return dag, blob_id
    return dag, None
