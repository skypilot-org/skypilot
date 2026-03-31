"""Custom format example: generate and process items from a range.

Demonstrates defining custom input/output formats *outside* of SkyPilot
source code.  The three custom formats live in ``custom_formats.py``:

- ``RangeInput``     — generates ``{'index': i}`` items (no file I/O)
- ``TextOutput``     — writes a ``.txt`` file per item
- ``JsonFileOutput`` — writes a ``.json`` metadata file per item

Usage (from project root):
    bash examples/batch/custom_formats/run.sh
"""
import os

# Import custom formats — this registers them with the global registries.
from custom_formats import JsonFileOutput  # pylint: disable=import-error
from custom_formats import RangeInput  # pylint: disable=import-error
from custom_formats import TextOutput  # pylint: disable=import-error

import sky
from sky.serve import serve_utils


@sky.batch.remote_function
def process_items():
    """Process each item: produce a text snippet and JSON metadata."""
    import random  # pylint: disable=import-outside-toplevel

    for batch in sky.batch.load():
        results = []
        for item in batch:
            idx = item['index']
            # Text output: concatenate some tokens
            tokens = [f'token_{j}' for j in range(idx, idx + 5)]
            text = f'Item {idx}: ' + ' | '.join(tokens)
            # Metadata output: some random fields
            metadata = {
                'id': idx,
                'squared': idx**2,
                'tag': random.choice(['alpha', 'beta', 'gamma']),
            }
            results.append({'text': text, 'metadata': metadata})
        sky.batch.save_results(results)


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
    meta_output_path = f's3://{bucket}/output/metadata/'

    # Create dataset from a range — no input file needed.
    ds = sky.batch.Dataset(RangeInput(count=20))

    ensure_pool(pool_name, pool_yaml)

    print('Processing 20 items on pool...')
    ds.map(
        process_items,
        pool_name=pool_name,
        batch_size=5,
        output=[
            TextOutput(text_output_path, column='text'),
            JsonFileOutput(meta_output_path, column='metadata'),
        ],
    )

    print(f'\nDone!  Text files  → {text_output_path}')
    print(f'       JSON files  → {meta_output_path}')


if __name__ == '__main__':
    main()
