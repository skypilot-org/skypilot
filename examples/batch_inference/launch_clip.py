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
        Tuple of (job_start_idx, job_end_idx)
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
                        default=100000,
                        help='Global end index in dataset')
    parser.add_argument('--num-jobs',
                        type=int,
                        default=10,
                        help='Number of jobs to partition the work across')
    parser.add_argument('--run-as-launch',
                        action='store_true',
                        help='Run as a launch')

    args = parser.parse_args()

    # Read HF_TOKEN from ~/.env
    env_path = os.path.expanduser('~/.env')
    hf_token = None
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith('HF_TOKEN='):
                    hf_token = line.strip().split('=')[1]
                    break

    if not hf_token:
        raise ValueError("HF_TOKEN not found in ~/.env")

    # Load the task template
    task = sky.Task.from_yaml('clip.yaml')

    # Launch jobs for each partition
    for job_rank in range(args.num_jobs):
        # Calculate index range for this job
        job_start, job_end = calculate_job_range(args.start_idx, args.end_idx,
                                                 job_rank, args.num_jobs)

        # Update environment variables for this job
        task_copy = task.update_envs({
            'START_IDX': job_start,
            'END_IDX': job_end,
            'HF_TOKEN': hf_token,
        })

        if not args.run_as_launch:
            # Launch the job
            sky.stream_and_get(sky.jobs.launch(
                task_copy,
                name=f'clip-inference-{job_start}-{job_end}',
                # detach_run=True,
                # retry_until_up=True,
            ))
        else:
            sky.launch(
                task_copy,
                retry_until_up=True,
            )


if __name__ == '__main__':
    main()
