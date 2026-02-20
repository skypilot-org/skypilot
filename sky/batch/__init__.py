"""Sky Batch: Distributed Batch Processing for SkyPilot.

This module provides APIs for distributed batch processing across cloud GPU
clusters. It enables scalable batch inference by distributing workloads
across a pool of workers.

Main components:
- dataset(): Create a Dataset from cloud storage
- remote_function: Decorator for functions that run on workers
- load(): Generator that yields batches on workers
- save_results(): Save results for the current batch

Example usage:
    import sky

    # Create dataset from cloud storage
    ds = sky.dataset("s3://bucket/data.jsonl")

    # Define mapper function
    @sky.batch.remote_function
    def process():
        for batch in sky.batch.load():
            results = [{"output": item["text"] * 2} for item in batch]
            sky.batch.save_results(results)

    # Apply pool and run
    pool_name = sky.jobs.pool_apply("pool.yaml")
    ds.map(process, pool_name=pool_name, batch_size=32,
           output_path="s3://bucket/output.jsonl")
"""
from sky.batch.api import load
from sky.batch.api import save_results
from sky.batch.dataset import Dataset
from sky.batch.dataset import dataset
from sky.batch.remote import remote_function

__all__ = [
    'Dataset',
    'dataset',
    'remote_function',
    'load',
    'save_results',
]
