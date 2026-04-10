"""Utility functions for Sky Batch.

Includes function serialization and cloud storage helpers.
"""
import base64
import inspect
import json
import os
import tempfile
import textwrap
import typing
from typing import Any, Callable, Dict, List, Optional, Tuple

from sky.adaptors import aws
from sky.adaptors import gcp
from sky.batch import constants
from sky.data import data_utils

if typing.TYPE_CHECKING:
    from sky.batch import io_formats


def serialize_function(fn: Callable) -> str:
    """Serialize a function to a base64-encoded string using source code.

    This approach is Python-version-agnostic, unlike cloudpickle which
    serializes bytecode that can be incompatible across Python versions
    (e.g., 3.11 vs 3.10).

    The function source code is extracted, along with metadata, and
    serialized as JSON. This works well for Sky Batch remote functions
    which are typically self-contained and use the sky.batch APIs.

    Args:
        fn: The function to serialize. Should be a module-level function
            or decorated with @sky.batch.remote_function.

    Returns:
        Base64-encoded JSON string containing the function source code
        and metadata.

    Raises:
        TypeError: If the function source cannot be retrieved.
    """
    # Get the source code
    try:
        source = inspect.getsource(fn)
    except (TypeError, OSError) as e:
        raise TypeError(
            f'Cannot serialize function {fn.__name__}: unable to retrieve '
            f'source code. Make sure the function is defined in a file '
            f'(not interactively) and is accessible to inspect.getsource(). '
            f'Error: {e}') from e

    # Remove leading indentation to make it a top-level definition
    source = textwrap.dedent(source)

    # Get the function name
    fn_name = fn.__name__

    # Package as JSON
    payload = {
        'type': 'source',
        'source': source,
        'name': fn_name,
        'version': '1.0',  # Serialization format version
    }

    serialized = json.dumps(payload)
    return base64.b64encode(serialized.encode('utf-8')).decode('utf-8')


def deserialize_function(serialized: str) -> Callable:
    """Deserialize a function from a base64-encoded string.

    Reconstructs the function by executing its source code in a clean
    namespace. The sky.batch module is made available for functions
    that use sky.batch.load() and sky.batch.save_results().

    Args:
        serialized: Base64-encoded JSON string from serialize_function().

    Returns:
        The deserialized function.

    Raises:
        ValueError: If the serialization format is unknown or invalid.
    """
    decoded = base64.b64decode(serialized.encode('utf-8'))
    payload = json.loads(decoded)

    if payload.get('type') != 'source':
        raise ValueError('Unknown or missing serialization type: '
                         f'{payload.get("type")}. Expected "source".')

    source = payload['source']
    fn_name = payload['name']

    # Execute the source code in a namespace that includes common imports
    # The sky.batch module will be available for worker functions
    namespace = {
        '__builtins__': __builtins__,
    }

    # Make sky.batch available (lazy import to avoid circular dependencies)
    try:
        import sky.batch  # pylint: disable=unused-import,import-outside-toplevel
        namespace['sky'] = __import__('sky')
    except ImportError:
        # If sky is not available, functions can still use explicit imports
        pass

    try:
        exec(source, namespace)  # pylint: disable=exec-used
    except Exception as e:
        raise ValueError(
            f'Failed to execute function source code for {fn_name}. '
            f'Error: {e}\n\nSource:\n{source}') from e

    if fn_name not in namespace:
        raise ValueError(
            f'Function {fn_name} not found in namespace after executing '
            f'source code. Available names: {list(namespace.keys())}')

    fn = namespace[fn_name]
    if not callable(fn):
        raise ValueError(
            f'Expected {fn_name} to be a callable function, but got '
            f'{type(fn).__name__}')

    return fn  # type: ignore[return-value]


def cloud_path_exists(path: str) -> bool:
    """Check if a cloud storage path exists.

    Args:
        path: Cloud storage path (e.g., 's3://bucket/path/file.jsonl')

    Returns:
        True if the path exists, False otherwise.
    """
    try:
        provider, bucket, key = parse_cloud_path(path)

        if provider == 's3':
            s3_client = aws.client('s3')
            try:
                s3_client.head_object(Bucket=bucket, Key=key)
                return True
            except s3_client.exceptions.NoSuchKey:  # type: ignore[attr-defined]
                return False
        elif provider == 'gs':
            client = gcp.storage_client()
            bucket_obj = client.bucket(bucket)
            blob = bucket_obj.blob(key)
            return blob.exists()
        else:
            return False
    except Exception:  # pylint: disable=broad-except
        # If we can't check, assume it doesn't exist (safer for confirmation)
        return False


def parse_cloud_path(path: str) -> Tuple[str, str, str]:
    """Parse a cloud storage path into provider, bucket, and key.

    Args:
        path: Cloud storage path (e.g., 's3://bucket/path/file.jsonl')

    Returns:
        Tuple of (provider, bucket_name, key_path).
        Provider is 's3' or 'gs'.

    Raises:
        ValueError: If the path format is invalid.
    """
    if path.startswith('s3://'):
        bucket, key = data_utils.split_s3_path(path)
        return ('s3', bucket, key)
    elif path.startswith('gs://'):
        bucket, key = data_utils.split_gcs_path(path)
        return ('gs', bucket, key)
    else:
        raise ValueError(f'Unsupported cloud storage path: {path}. '
                         'Supported prefixes: s3://, gs://')


def download_from_cloud(path: str, dest: str) -> None:
    """Download a file from cloud storage to a local path.

    Args:
        path: Cloud storage path (s3:// or gs://).
        dest: Local destination file path.
    """
    provider, bucket, key = parse_cloud_path(path)
    _download_file(provider, bucket, key, dest)


def load_jsonl_from_cloud(path: str) -> List[Dict[str, Any]]:
    """Load a JSONL file from cloud storage.

    Args:
        path: Cloud storage path to the JSONL file.

    Returns:
        List of dictionaries, one per line in the JSONL file.
    """
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.jsonl',
                                     delete=False) as f:
        temp_path = f.name

    try:
        download_from_cloud(path, temp_path)
        return _load_jsonl_file(temp_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _download_file(provider: str, bucket: str, key: str,
                   local_path: str) -> None:
    """Download a file from cloud storage.

    Args:
        provider: Cloud provider ('s3', 'gs').
        bucket: Bucket name.
        key: Object key/path within the bucket.
        local_path: Local path to save the file.
    """
    if provider == 's3':
        _download_from_s3(bucket, key, local_path)
    elif provider == 'gs':
        _download_from_gcs(bucket, key, local_path)
    else:
        raise ValueError(f'Unsupported provider: {provider}')


def _download_from_s3(bucket: str, key: str, local_path: str) -> None:
    """Download a file from S3."""
    s3 = aws.resource('s3')
    s3.Bucket(bucket).download_file(key, local_path)


def _download_from_gcs(bucket: str, key: str, local_path: str) -> None:
    """Download a file from GCS."""
    client = gcp.storage_client()
    bucket_obj = client.bucket(bucket)
    blob = bucket_obj.blob(key)
    blob.download_to_filename(local_path)


def download_file_from_cloud(cloud_path: str, local_path: str) -> None:
    """Download a file from cloud storage.

    Args:
        cloud_path: Cloud storage path to download from.
        local_path: Local destination path.
    """
    provider, bucket, key = parse_cloud_path(cloud_path)

    if provider == 's3':
        s3 = aws.client('s3')
        s3.download_file(bucket, key, local_path)
    elif provider == 'gs':
        client = gcp.storage_client()
        bucket_obj = client.bucket(bucket)
        blob = bucket_obj.blob(key)
        blob.download_to_filename(local_path)
    else:
        raise ValueError(f'Unsupported provider: {provider}')


def upload_file_to_cloud(local_path: str, cloud_path: str) -> None:
    """Upload a file to cloud storage.

    Args:
        local_path: Local path of the file to upload.
        cloud_path: Cloud storage destination path.
    """
    provider, bucket, key = parse_cloud_path(cloud_path)

    if provider == 's3':
        _upload_to_s3(local_path, bucket, key)
    elif provider == 'gs':
        _upload_to_gcs(local_path, bucket, key)
    else:
        raise ValueError(f'Unsupported provider: {provider}')


def _upload_to_s3(local_path: str, bucket: str, key: str) -> None:
    """Upload a file to S3."""
    s3 = aws.resource('s3')
    s3.Bucket(bucket).upload_file(local_path, key)


def _upload_to_gcs(local_path: str, bucket: str, key: str) -> None:
    """Upload a file to GCS."""
    client = gcp.storage_client()
    bucket_obj = client.bucket(bucket)
    blob = bucket_obj.blob(key)
    blob.upload_from_filename(local_path)


def upload_bytes_to_cloud(data: bytes, cloud_path: str) -> None:
    """Upload raw bytes to cloud storage.

    Args:
        data: Bytes to upload.
        cloud_path: Cloud storage destination path.
    """
    provider, bucket, key = parse_cloud_path(cloud_path)

    if provider == 's3':
        s3 = aws.client('s3')
        s3.put_object(Bucket=bucket, Key=key, Body=data)
    elif provider == 'gs':
        client = gcp.storage_client()
        bucket_obj = client.bucket(bucket)
        blob = bucket_obj.blob(key)
        blob.upload_from_string(data, content_type='application/octet-stream')
    else:
        raise ValueError(f'Unsupported provider: {provider}')


def _load_jsonl_file(path: str) -> List[Dict[str, Any]]:
    """Load a local JSONL file.

    Args:
        path: Path to the JSONL file.

    Returns:
        List of dictionaries, one per line.
    """
    data = []
    with open(path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(
                    f'Invalid JSON at line {line_num} in {path}: {e}') from e
    return data


def save_jsonl_to_cloud(data: List[Dict[str, Any]], cloud_path: str) -> None:
    """Save data as a JSONL file to cloud storage.

    Args:
        data: List of dictionaries to save.
        cloud_path: Cloud storage destination path.
    """
    with tempfile.NamedTemporaryFile(mode='w',
                                     suffix='.jsonl',
                                     delete=False,
                                     encoding='utf-8') as f:
        temp_path = f.name
        for item in data:
            f.write(json.dumps(item) + '\n')

    try:
        upload_file_to_cloud(temp_path, cloud_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def get_input_batch_path(output_path: str,
                         start_idx: int,
                         end_idx: int,
                         job_id: Optional[str] = None) -> str:
    """Generate an input batch file path for intermediate input data.

    Args:
        output_path: Final output path (e.g., 's3://bucket/output.jsonl').
        start_idx: Starting index of the batch.
        end_idx: Ending index of the batch (inclusive).
        job_id: Optional job ID for namespacing.

    Returns:
        Cloud path for the input batch file.
    """
    provider, bucket, key = parse_cloud_path(output_path)
    base_dir = os.path.dirname(key)
    if base_dir:
        base_dir += '/'

    if job_id:
        temp_dir = f'{base_dir}{constants.TEMP_DIR_NAME}/{job_id}'
    else:
        temp_dir = f'{base_dir}{constants.TEMP_DIR_NAME}'

    batch_name = constants.INPUT_BATCH_NAME_PATTERN.format(start=start_idx,
                                                           end=end_idx)
    batch_key = f'{temp_dir}/{batch_name}'

    if provider == 's3':
        return f's3://{bucket}/{batch_key}'
    elif provider == 'gs':
        return f'gs://{bucket}/{batch_key}'
    else:
        raise ValueError(f'Unsupported provider: {provider}')


def get_batch_path(output_path: str,
                   start_idx: int,
                   end_idx: int,
                   job_id: Optional[str] = None) -> str:
    """Generate a batch file path for intermediate results.

    Args:
        output_path: Final output path (e.g., 's3://bucket/output.jsonl').
        start_idx: Starting index of the batch.
        end_idx: Ending index of the batch (inclusive).
        job_id: Optional job ID for namespacing.

    Returns:
        Cloud path for the batch file.
    """
    provider, bucket, key = parse_cloud_path(output_path)
    base_dir = os.path.dirname(key)
    if base_dir:
        base_dir += '/'

    if job_id:
        temp_dir = f'{base_dir}{constants.TEMP_DIR_NAME}/{job_id}'
    else:
        temp_dir = f'{base_dir}{constants.TEMP_DIR_NAME}'

    batch_name = constants.BATCH_NAME_PATTERN.format(start=start_idx,
                                                     end=end_idx)
    batch_key = f'{temp_dir}/{batch_name}'

    if provider == 's3':
        return f's3://{bucket}/{batch_key}'
    elif provider == 'gs':
        return f'gs://{bucket}/{batch_key}'
    else:
        raise ValueError(f'Unsupported provider: {provider}')


def list_batch_files(output_path: str,
                     job_id: Optional[str] = None) -> List[str]:
    """List all batch files for a job.

    Args:
        output_path: Final output path.
        job_id: Optional job ID for namespacing.

    Returns:
        List of batch file paths, sorted by starting index.
    """
    provider, bucket, key = parse_cloud_path(output_path)
    base_dir = os.path.dirname(key)
    if base_dir:
        base_dir += '/'

    if job_id:
        prefix = f'{base_dir}{constants.TEMP_DIR_NAME}/{job_id}/'
    else:
        prefix = f'{base_dir}{constants.TEMP_DIR_NAME}/'

    if provider == 's3':
        objects = _list_s3_objects(bucket, prefix)
    elif provider == 'gs':
        objects = _list_gcs_objects(bucket, prefix)
    else:
        raise ValueError(f'Unsupported provider: {provider}')

    # Filter to only result batch files (exclude input_batch_* files)
    batch_files = [
        c for c in objects
        if c.endswith('.jsonl') and os.path.basename(c).startswith('batch_')
    ]
    batch_files.sort(key=_extract_batch_start_index)

    # Convert back to full cloud paths
    if provider == 's3':
        return [f's3://{bucket}/{c}' for c in batch_files]
    elif provider == 'gs':
        return [f'gs://{bucket}/{c}' for c in batch_files]
    return []


def _extract_batch_start_index(batch_path: str) -> int:
    """Extract the starting index from a batch file path."""
    # batch_00000000-00000031.jsonl -> 0
    filename = os.path.basename(batch_path)
    if filename.startswith('batch_') and '-' in filename:
        start_str = filename.split('_')[1].split('-')[0]
        try:
            return int(start_str)
        except ValueError:
            pass
    return 0


def _list_s3_objects(bucket: str, prefix: str) -> List[str]:
    """List objects in an S3 bucket with a prefix."""
    s3 = aws.client('s3')
    paginator = s3.get_paginator('list_objects_v2')
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            keys.append(obj['Key'])
    return keys


def _list_gcs_objects(bucket: str, prefix: str) -> List[str]:
    """List objects in a GCS bucket with a prefix."""
    client = gcp.storage_client()
    bucket_obj = client.bucket(bucket)
    blobs = bucket_obj.list_blobs(prefix=prefix)
    return [blob.name for blob in blobs]


def delete_batch_files(output_path: str, job_id: Optional[str] = None) -> None:
    """Delete all batch files for a specific job.

    IMPORTANT: Always provide job_id to avoid deleting files from other jobs
    when multiple batch jobs share the same bucket.

    Args:
        output_path: Final output path.
        job_id: Job ID for namespacing. REQUIRED for safety in multi-job
                environments.

    Raises:
        ValueError: If job_id is not provided.
    """
    if job_id is None:
        raise ValueError('job_id is required for delete_batch_files().')

    batch_files = list_batch_files(output_path, job_id)
    for batch_path in batch_files:
        provider, bucket, key = parse_cloud_path(batch_path)
        if provider == 's3':
            _delete_s3_object(bucket, key)
        elif provider == 'gs':
            _delete_gcs_object(bucket, key)


def delete_input_batch_files(output_path: str,
                             job_id: Optional[str] = None) -> None:
    """Delete all input batch files for a specific job.

    IMPORTANT: Always provide job_id to avoid deleting files from other jobs
    when multiple batch jobs share the same bucket.

    Lists objects in the job-specific temp directory and deletes those matching
    the ``input_batch_*`` prefix.

    Args:
        output_path: Final output path.
        job_id: Job ID for namespacing. REQUIRED for safety in multi-job
                environments.

    Raises:
        ValueError: If job_id is not provided.
    """
    if job_id is None:
        raise ValueError('job_id is required for delete_input_batch_files().')

    provider, bucket, key = parse_cloud_path(output_path)
    base_dir = os.path.dirname(key)
    if base_dir:
        base_dir += '/'

    # Only delete files in the job-specific subdirectory
    prefix = f'{base_dir}{constants.TEMP_DIR_NAME}/{job_id}/'

    if provider == 's3':
        all_objects = _list_s3_objects(bucket, prefix)
    elif provider == 'gs':
        all_objects = _list_gcs_objects(bucket, prefix)
    else:
        raise ValueError(f'Unsupported provider: {provider}')

    input_batches = [
        obj for obj in all_objects
        if os.path.basename(obj).startswith('input_batch_')
    ]

    for obj_key in input_batches:
        if provider == 's3':
            _delete_s3_object(bucket, obj_key)
        elif provider == 'gs':
            _delete_gcs_object(bucket, obj_key)


def _delete_s3_object(bucket: str, key: str) -> None:
    """Delete an object from S3."""
    s3 = aws.client('s3')
    s3.delete_object(Bucket=bucket, Key=key)


def _delete_gcs_object(bucket: str, key: str) -> None:
    """Delete an object from GCS."""
    client = gcp.storage_client()
    bucket_obj = client.bucket(bucket)
    blob = bucket_obj.blob(key)
    blob.delete()


def concatenate_batches_to_output(output_path: str,
                                  job_id: Optional[str] = None) -> None:
    """Concatenate all batch files into the final output file.

    IMPORTANT: Always provide job_id to ensure only this job's temp files are
    processed when multiple batch jobs share the same bucket.

    Batches are processed in order based on their starting indices to maintain
    the original data order. Temp file cleanup is handled separately by
    ``OutputWriter.cleanup()``.

    Args:
        output_path: Final output path.
        job_id: Job ID for namespacing. REQUIRED for safety in multi-job
                environments.

    Raises:
        ValueError: If job_id is not provided.
    """
    if job_id is None:
        raise ValueError(
            'job_id is required for concatenate_batches_to_output() to '
            'prevent accidentally processing or deleting files from other '
            'jobs sharing the same bucket.')

    batch_files = list_batch_files(output_path, job_id)
    if not batch_files:
        return

    # Download and concatenate all batches
    all_data: List[Dict[str, Any]] = []
    for batch_path in batch_files:
        batch_data = load_jsonl_from_cloud(batch_path)
        all_data.extend(batch_data)

    # Upload the concatenated result
    save_jsonl_to_cloud(all_data, output_path)
