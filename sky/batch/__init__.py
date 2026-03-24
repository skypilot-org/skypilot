"""Sky Batch: Distributed Batch Processing for SkyPilot.

This module provides APIs for distributed batch processing across cloud GPU
clusters. It enables scalable batch inference by distributing workloads
across a pool of workers.

Main components:
- Dataset(): Create a Dataset from a typed InputFormat
- JsonInput / JsonOutput / ImageOutput: Typed format descriptors
- remote_function: Decorator for functions that run on workers
- load(): Generator that yields batches on workers
- save_results(): Save results for the current batch

Example usage:
    import sky

    # Create dataset from cloud storage
    ds = sky.batch.Dataset(sky.batch.JsonInput("s3://bucket/data.jsonl"))

    # Define mapper function
    @sky.batch.remote_function
    def process():
        for batch in sky.batch.load():
            results = [{"output": item["text"] * 2} for item in batch]
            sky.batch.save_results(results)

    # Apply pool and run
    pool_name = sky.jobs.pool_apply("pool.yaml")
    ds.map(process, pool_name=pool_name, batch_size=32,
           output=sky.batch.JsonOutput("s3://bucket/output.jsonl"))
"""
from sky.batch.dataset import Dataset
from sky.batch.io_formats import ImageOutput
from sky.batch.io_formats import InputFormat
from sky.batch.io_formats import JsonInput
from sky.batch.io_formats import JsonOutput
from sky.batch.io_formats import OutputFormat
from sky.batch.remote import remote_function
from sky.batch.worker import load
from sky.batch.worker import save_results

__all__ = [
    'Dataset',
    'remote_function',
    'load',
    'save_results',
    'InputFormat',
    'OutputFormat',
    'JsonInput',
    'JsonOutput',
    'ImageOutput',
]
