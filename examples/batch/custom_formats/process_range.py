"""Custom format example: generate and process items from a range.

Demonstrates defining custom input/output formats *outside* of SkyPilot
source code.  Custom formats are defined inline below:

- ``RangeReader``   -- generates ``{'index': i}`` items (no file I/O)
- ``TextWriter``   -- writes a ``.txt`` file per item (per-item pattern)
- ``YamlWriter``   -- writes a single merged ``.yaml`` file (batch+merge)

Usage (from project root):
    bash examples/batch/custom_formats/run.sh
"""
from dataclasses import dataclass
import logging
import os
from typing import Any, Dict, List

import sky
from sky.batch import io_formats
from sky.batch import utils
from sky.serve import serve_utils
from sky.utils import registry

logger = logging.getLogger(__name__)

# ---- Custom formats ----------------------------------------------------------


@registry.INPUT_READER_REGISTRY.type_register(name='range')
@dataclass
class RangeReader(io_formats.InputReader):
    """Generate items from a Python ``range`` -- no file I/O needed."""

    count: int

    def __len__(self) -> int:
        return self.count

    def download_batch(self, start_idx: int, end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        return [{'index': i} for i in range(start_idx, end_idx + 1)]


@registry.OUTPUT_WRITER_REGISTRY.type_register(name='text')
@dataclass
class TextWriter(io_formats.OutputWriter):
    """Per-item ``.txt`` file output."""

    column: str

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError('TextWriter path cannot be empty')
        if not self.path.startswith(('s3://', 'gs://')):
            raise ValueError(f'Unsupported storage path: {self.path}')
        if not self.path.endswith('/'):
            raise ValueError(f'TextWriter path must end with /: {self.path}')

    def upload_batch(self, results: List[Dict[str, Any]], start_idx: int,
                     end_idx: int, job_id: str) -> str:
        output_dir = self.path.rstrip('/')
        for i, result in enumerate(results):
            global_idx = start_idx + i
            text = str(result.get(self.column, ''))
            cloud_path = f'{output_dir}/{global_idx:08d}.txt'
            utils.upload_bytes_to_cloud(text.encode('utf-8'), cloud_path)
        return output_dir

    def reduce_results(self, job_id: str) -> None:
        pass

    def cleanup(self, job_id: str) -> None:
        pass


@registry.OUTPUT_WRITER_REGISTRY.type_register(name='yaml')
@dataclass
class YamlWriter(io_formats.OutputWriter):
    """Single merged YAML file output (batch + merge pattern)."""

    column: str

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError('YamlWriter path cannot be empty')
        if not self.path.startswith(('s3://', 'gs://')):
            raise ValueError(f'Unsupported storage path: {self.path}')
        if not self.path.endswith('.yaml'):
            raise ValueError(
                f'YamlWriter path must end with .yaml: {self.path}')

    def upload_batch(self, results: List[Dict[str, Any]], start_idx: int,
                     end_idx: int, job_id: str) -> str:
        batch_path = utils.get_batch_path(self.path, start_idx, end_idx, job_id)
        filtered = [{self.column: r.get(self.column)} for r in results]
        utils.save_jsonl_to_cloud(filtered, batch_path)
        return batch_path

    def reduce_results(self, job_id: str) -> None:
        import yaml  # pylint: disable=import-outside-toplevel
        all_items: List[Dict[str, Any]] = []
        for batch_path in utils.list_batch_files(self.path, job_id):
            all_items.extend(utils.load_jsonl_from_cloud(batch_path))
        yaml_bytes = yaml.dump(all_items, default_flow_style=False).encode()
        utils.upload_bytes_to_cloud(yaml_bytes, self.path)

    def cleanup(self, job_id: str) -> None:
        utils.delete_batch_files(self.path, job_id)
        utils.delete_input_batch_files(self.path, job_id)


# ---- Mapper function ---------------------------------------------------------


@sky.batch.remote_function
def process_items():
    """Process each item: produce a text snippet and YAML metadata."""
    import random  # pylint: disable=import-outside-toplevel

    for batch in sky.batch.load():
        results = []
        for item in batch:
            idx = item['index']
            tokens = [f'token_{j}' for j in range(idx, idx + 5)]
            text = f'Item {idx}: ' + ' | '.join(tokens)
            metadata = {
                'id': idx,
                'squared': idx**2,
                'tag': random.choice(['alpha', 'beta', 'gamma']),
            }
            results.append({'text': text, 'metadata': metadata})
        sky.batch.save_results(results)


# ---- Main --------------------------------------------------------------------


def ensure_pool(pool_name: str, pool_yaml: str) -> None:
    """Create the pool if it doesn't already exist."""
    try:
        request_id = sky.jobs.pool_status([pool_name])
        pool_statuses = sky.stream_and_get(request_id)
        if pool_statuses:
            print(f'Pool {pool_name} already exists, skipping pool apply.')
            return
    except sky.exceptions.ClusterNotUpError:
        pass

    print(f'Pool {pool_name} not found. Creating from {pool_yaml}...')
    task = sky.Task.from_yaml(pool_yaml)
    task.name = pool_name
    request_id = sky.jobs.pool_apply(
        task,
        pool_name=pool_name,
        mode=serve_utils.DEFAULT_UPDATE_MODE,
    )
    sky.stream_and_get(request_id)
    print(f'Pool {pool_name} is ready.')


def main():
    default_bucket = (
        f'sky-batch-custom-fmt-{os.environ.get("USER", "default")}')
    bucket = os.environ.get('SKY_BATCH_BUCKET', default_bucket)
    print(f'Using bucket: s3://{bucket}  '
          f'(override with SKY_BATCH_BUCKET env var)')

    pool_name = 'custom-fmt-pool'
    pool_yaml = os.path.join(os.path.dirname(__file__), 'pool.yaml')

    text_output_path = f's3://{bucket}/output/texts/'
    meta_output_path = f's3://{bucket}/output/metadata.yaml'

    ds = sky.batch.Dataset(RangeReader(path='', count=20))

    ensure_pool(pool_name, pool_yaml)

    print('Processing 20 items on pool...')
    ds.map(
        process_items,
        pool_name=pool_name,
        batch_size=5,
        output=[
            TextWriter(text_output_path, column='text'),
            YamlWriter(meta_output_path, column='metadata'),
        ],
    )

    print(f'\nDone!  Text files  → {text_output_path}')
    print(f'       YAML file   → {meta_output_path}')


if __name__ == '__main__':
    main()
