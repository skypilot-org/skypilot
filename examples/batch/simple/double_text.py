"""Simple batch processing example: double text strings.

This example demonstrates the basic usage of Sky Batch for distributed
processing. It takes a JSONL file where each line has a "text" field,
and outputs a JSONL file where each line has an "output" field containing
the doubled text.

Usage (from project root):
    1. Prepare your dataset (run.sh creates 50 items automatically):
       $ bash examples/batch/simple/run.sh

    2. Run this script:
       $ python examples/batch/simple/double_text.py

    3. Check the output:
       $ aws s3 cp s3://my-bucket/output.jsonl /tmp/output.jsonl
       $ cat /tmp/output.jsonl
       {"output": "hellohello"}
       {"output": "worldworld"}
"""
import os

import sky
from sky.batch import utils
from sky.serve import serve_utils


# Define the mapper function that runs on workers
@sky.batch.remote_function
def double_text():
    """Double each text string in the input batch.

    This function runs on each worker. It receives batches of data
    via sky.batch.load() and saves results via sky.batch.save_results().
    """
    # Process batches continuously until no more data
    for batch in sky.batch.load():
        # batch is a list of dicts, e.g., [{"text": "hello"}, ...]
        print(f'Processing batch: {batch}')
        results = []
        for item in batch:
            text = item.get('text', '')
            results.append({'text': text, 'output': text * 2})

        # Save results (order must match input batch order)
        sky.batch.save_results(results)


def ensure_pool(pool_name: str, pool_yaml: str) -> None:
    """Create the pool if it doesn't already exist."""
    request_id = sky.jobs.pool_status([pool_name])
    pool_statuses = sky.stream_and_get(request_id)
    if pool_statuses:
        print(f'Pool {pool_name} already exists, skipping pool apply.')
        return

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
    # Configuration — set SKY_BATCH_BUCKET env var or use the default.
    default_bucket = f'sky-batch-simple-{os.environ.get("USER", "default")}'
    bucket = os.environ.get('SKY_BATCH_BUCKET', default_bucket)
    print(f'Using bucket: s3://{bucket}  '
          f'(override with SKY_BATCH_BUCKET env var)')

    input_path = f's3://{bucket}/test.jsonl'
    output_path = f's3://{bucket}/output.jsonl'
    pool_name = 'test-batch-pool'
    pool_yaml = os.path.join(os.path.dirname(__file__), 'pool.yaml')

    # Create dataset from cloud storage
    ds = sky.dataset(sky.batch.JsonInput(input_path))

    # Ensure the pool exists (creates it if needed)
    ensure_pool(pool_name, pool_yaml)

    # Process the dataset.
    # ds.map() submits the job to the pool's batch controller via HTTP
    # and polls until all batches are completed.
    print(f'Processing dataset {input_path} on pool {pool_name}...')
    ds.map(
        double_text,
        pool_name=pool_name,
        batch_size=2,  # Process 2 items per batch
        output=sky.batch.JsonOutput(output_path),
    )

    print(f'Done! Results written to {output_path}')

    # Download and print the results
    print('\n' + '=' * 60)
    print('RESULTS:')
    print('=' * 60)
    try:
        results = utils.load_jsonl_from_cloud(output_path)
        for i, result in enumerate(results, 1):
            print(f'{i}. {result}')
        print('=' * 60)
        print(f'Total results: {len(results)}')
    except Exception as e:  # pylint: disable=broad-except
        print(f'Error loading results: {e}')
        print(f'You can manually download the results from: {output_path}')


if __name__ == '__main__':
    main()
