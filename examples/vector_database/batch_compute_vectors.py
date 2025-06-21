"""
Use skypilot to launch managed jobs that will run the embedding calculation.  

This script is responsible for splitting the input dataset up among several workers,  
then using skypilot to launch managed jobs for each worker. We use compute_vectors.yaml  
to define the managed job info.
"""

#!/usr/bin/env python3

import argparse
import os

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
    parser.add_argument('--end-idx',
                        type=int,
                        default=1000000,
                        help='Global end index in dataset, not inclusive')
    parser.add_argument('--num-jobs',
                        type=int,
                        default=100,
                        help='Number of jobs to partition the work across')
    parser.add_argument('--env-path',
                        type=str,
                        default='~/.env',
                        help='Path to the environment file')
    args = parser.parse_args()

    # Try to get HF_TOKEN from environment first, then ~/.env file
    hf_token = os.environ.get('HF_TOKEN')
    if not hf_token:
        env_path = os.path.expanduser(args.env_path)
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith('HF_TOKEN='):
                        hf_token = line.strip().split('=')[1]
                        break

    if not hf_token:
        raise ValueError("HF_TOKEN not found in ~/.env or environment variable")

    # Load the task template
    task = sky.Task.from_yaml('compute_vectors.yaml')

    # Launch jobs for each partition
    for job_rank in range(args.num_jobs):
        # Calculate index range for this job
        job_start, job_end = calculate_job_range(args.start_idx, args.end_idx,
                                                 job_rank, args.num_jobs)

        # Update environment variables for this job
        task_copy = task.update_envs({
            'START_IDX': job_start,
            'END_IDX': job_end,
        })
        task_copy = task_copy.update_secrets({
            'HF_TOKEN': hf_token,
        })

        sky.jobs.launch(
            task_copy,
            name=f'vector-compute-{job_start}-{job_end}',
        )


if __name__ == '__main__':
    main()
