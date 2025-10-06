import argparse
import os

import sky


def calculate_job_range(total_records, job_rank, total_jobs):
    chunk_size = total_records // total_jobs
    remainder = total_records % total_jobs

    job_start = job_rank * chunk_size + min(job_rank, remainder)
    if job_rank < remainder:
        chunk_size += 1

    return job_start, job_start + chunk_size


def load_env_file(env_file):
    env_vars = {}
    if not os.path.exists(env_file):
        return env_vars

    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                if line.startswith('export '):
                    line = line[7:]
                if '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key] = value.strip('"').strip("'")
    return env_vars


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--total-records', type=int, default=1000000)
    parser.add_argument('--num-jobs', type=int, default=5)
    parser.add_argument('--batch-size', type=int, default=10240)
    parser.add_argument('--env-file', type=str, default='.env')
    args = parser.parse_args()

    env_vars = load_env_file(args.env_file)

    required = ['REDIS_HOST', 'REDIS_PORT', 'REDIS_USER', 'REDIS_PASSWORD']
    missing = [var for var in required if var not in env_vars]
    if missing:
        raise ValueError(f"Missing required environment variables: {missing}")

    task = sky.Task.from_yaml('embedding_job.yaml')

    for i in range(args.num_jobs):
        start, end = calculate_job_range(args.total_records, i, args.num_jobs)

        task_envs = task.update_envs({
            'JOB_START_IDX': str(start),
            'JOB_END_IDX': str(end),
            'BATCH_SIZE': str(args.batch_size),
            'REDIS_HOST': env_vars['REDIS_HOST'],
            'REDIS_PORT': env_vars['REDIS_PORT'],
            'REDIS_USER': env_vars['REDIS_USER'],
            'REDIS_PASSWORD': env_vars['REDIS_PASSWORD']
        })

        sky.jobs.launch(task_envs, name=f'embeddings-{start}-{end}')
        print(f"Launched job for records {start}-{end}")

    print(f"\n{args.num_jobs} jobs launched successfully!")
    print("Monitor with: sky jobs queue")


if __name__ == '__main__':
    main()
