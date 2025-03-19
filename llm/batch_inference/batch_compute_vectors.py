"""
Use skypilot to launch managed jobs that will run the embedding calculation.  

This script is responsible for:
1. Launching a monitoring service cluster
2. Splitting the input dataset among several workers
3. Launching worker clusters with unique worker IDs
"""

#!/usr/bin/env python3

import argparse
import os
import time
import uuid

import sky


def calculate_job_range(start_idx: int, end_idx: int, job_rank: int,
                        total_jobs: int) -> tuple[int, int]:
    """Calculate the range of indices this job should process.
    
    Args:
        start_idx: Global start index
        end_idx: Global end index
        job_rank: Current job's rank (0-based)
        total_jobs: Total number of jobs
        
    Returns:
        Tuple of [job_start_idx, job_end_idx)
    """
    total_range = end_idx - start_idx
    chunk_size = total_range // total_jobs
    remainder = total_range % total_jobs

    # Distribute remainder across first few jobs
    job_start = start_idx + (job_rank * chunk_size) + min(job_rank, remainder)
    if job_rank < remainder:
        chunk_size += 1
    job_end = job_start + chunk_size

    return job_start, job_end


def main():
    parser = argparse.ArgumentParser(
        description='Launch batch CLIP inference jobs')
    parser.add_argument('--start-idx',
                        type=int,
                        default=0,
                        help='Global start index in dataset')
    parser.add_argument(
        '--end-idx',
        type=int,
        default=29475453,
        #29475453 is the last index of the review dataset
        help='Global end index in dataset, not inclusive')
    parser.add_argument('--num-jobs',
                        type=int,
                        default=500,
                        help='Number of jobs to partition the work across')
    parser.add_argument('--skip-monitor',
                        action='store_true',
                        help='Skip launching the monitoring service')
    parser.add_argument('--bucket-name',
                        type=str,
                        default='sky-embeddings',
                        help='Name of the bucket to store embeddings')
    parser.add_argument(
        '--partition-method',
        type=str,
        choices=['chunk', 'stride'],
        default='stride',
        help=
        'Method to partition data: chunk (contiguous) or stride (interleaved)')
    args = parser.parse_args()

    # Load the worker task template
    task = sky.Task.from_yaml('compute_text_vectors.yaml')

    # Launch jobs for each partition
    for job_rank in range(args.num_jobs):
        # Create a unique worker ID
        worker_id = f"worker_{job_rank}"

        # Update environment variables based on partition method
        env_vars = {
            'WORKER_ID': worker_id,
            'PARTITION_METHOD': args.partition_method,
            'WORKER_RANK': str(job_rank),
            'TOTAL_WORKERS': str(args.num_jobs),
            'GLOBAL_START_IDX': str(args.start_idx),
            'GLOBAL_END_IDX': str(args.end_idx),
        }

        # If using chunk method, also provide start_idx and end_idx
        if args.partition_method == 'chunk':
            job_start, job_end = calculate_job_range(args.start_idx,
                                                     args.end_idx, job_rank,
                                                     args.num_jobs)
            env_vars['START_IDX'] = str(job_start)
            env_vars['END_IDX'] = str(job_end)
            job_name = f'vector-compute-{job_start}-{job_end}'
        else:
            # For stride method, we use the global start/end and let the worker handle striding
            job_name = f'vector-compute-worker-{job_rank}'

        task_copy = task.update_envs(env_vars)

        print(f"Launching {job_name} with {worker_id}...")

        sky.jobs.launch(
            task_copy,
            name=job_name,
        )


if __name__ == '__main__':
    main()
