#!/usr/bin/env python3
"""
Inject test managed jobs into PostgreSQL database for scale testing.
This script should be run from within the skypilot-api-server pod.
"""

import copy
import os
import random
import time

try:
    import psycopg2
    from psycopg2.extras import execute_batch
except ImportError:
    print("Error: psycopg2 not installed. Install with: pip install psycopg2-binary")
    exit(1)


def load_managed_job_sample(conn, managed_job_id=9):
    """Load a managed job from PostgreSQL as a template."""
    cursor = conn.cursor()

    # Load from spot table
    cursor.execute("SELECT * FROM spot WHERE job_id = %s", (managed_job_id,))
    spot_columns = [desc[0] for desc in cursor.description]
    spot_row = cursor.fetchone()

    if not spot_row:
        raise ValueError(
            f"Managed job with ID {managed_job_id} not found in spot table. "
            f"Please create a sample job first."
        )

    spot_sample = dict(zip(spot_columns, spot_row))

    # Load from job_info table
    cursor.execute("SELECT * FROM job_info WHERE spot_job_id = %s",
                   (spot_sample['spot_job_id'],))
    job_info_columns = [desc[0] for desc in cursor.description]
    job_info_row = cursor.fetchone()

    if not job_info_row:
        raise ValueError(
            f"Job info for spot_job_id {spot_sample['spot_job_id']} not found."
        )

    job_info_sample = dict(zip(job_info_columns, job_info_row))
    cursor.close()

    return spot_sample, job_info_sample, spot_columns, job_info_columns


def generate_managed_job_data(spot_sample, job_info_sample, count, max_job_id):
    """Generate managed job data by cloning the sample job."""
    spot_jobs = []
    job_infos = []

    current_time = time.time()
    starting_job_id = max_job_id + 1

    for i in range(count):
        # Deep copy the samples
        spot_job = copy.deepcopy(spot_sample)
        job_info = copy.deepcopy(job_info_sample)

        # Update unique IDs
        job_id = starting_job_id + i
        spot_job_id = starting_job_id + i

        spot_job['job_id'] = job_id
        spot_job['spot_job_id'] = spot_job_id
        job_info['spot_job_id'] = spot_job_id

        # Update timestamps
        base_time = current_time - random.uniform(0, 3600)  # Within last hour
        spot_job['submitted_at'] = base_time
        spot_job['start_at'] = base_time + random.uniform(30, 120)
        spot_job['last_recovered_at'] = spot_job['start_at']

        # Update run_timestamp to be unique
        timestamp_str = time.strftime('%Y-%m-%d-%H-%M-%S',
                                      time.localtime(base_time))
        spot_job['run_timestamp'] = f'sky-{timestamp_str}-{random.randint(100000, 999999)}'

        # Update controller_pid to be unique
        job_info['controller_pid'] = -(random.randint(1000, 99999))

        # Update file paths to be unique
        home_dir = os.path.expanduser('~')
        job_hash = f'{i+1:04d}'
        job_info['dag_yaml_path'] = f'{home_dir}/.sky/managed_jobs/test-job-{job_hash}.yaml'
        job_info['env_file_path'] = f'{home_dir}/.sky/managed_jobs/test-job-{job_hash}.env'
        job_info['original_user_yaml_path'] = f'{home_dir}/.sky/managed_jobs/test-job-{job_hash}.original_user_yaml'

        spot_jobs.append(spot_job)
        job_infos.append(job_info)

    return spot_jobs, job_infos


def inject_managed_jobs(conn, count=10000, managed_job_id=9):
    """Inject test managed jobs into PostgreSQL database."""
    print(f"Injecting {count} test managed jobs...")

    # Load sample data
    print("Loading sample managed job...")
    spot_sample, job_info_sample, spot_columns, job_info_columns = load_managed_job_sample(
        conn, managed_job_id
    )

    # Find max job_id
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(job_id) FROM spot")
    max_job_id = cursor.fetchone()[0] or 0
    cursor.close()

    print(f"Current max job_id: {max_job_id}")
    print(f"Generating {count} test jobs starting from job_id {max_job_id + 1}...")

    # Generate test data
    spot_jobs, job_infos = generate_managed_job_data(
        spot_sample, job_info_sample, count, max_job_id
    )

    # Prepare insert statements
    spot_columns_str = ', '.join(spot_columns)
    spot_placeholders = ', '.join(['%s' for _ in spot_columns])
    job_info_columns_str = ', '.join(job_info_columns)
    job_info_placeholders = ', '.join(['%s' for _ in job_info_columns])

    spot_insert_sql = f"INSERT INTO spot ({spot_columns_str}) VALUES ({spot_placeholders})"
    job_info_insert_sql = f"INSERT INTO job_info ({job_info_columns_str}) VALUES ({job_info_placeholders})"

    # Insert in batches for performance
    batch_size = 100
    total_inserted = 0

    cursor = conn.cursor()

    print(f"Inserting jobs in batches of {batch_size}...")
    for i in range(0, len(spot_jobs), batch_size):
        spot_batch = spot_jobs[i:i + batch_size]
        job_info_batch = job_infos[i:i + batch_size]

        # Convert dicts to tuples in column order
        spot_batch_data = [tuple(job[col] for col in spot_columns) for job in spot_batch]
        job_info_batch_data = [tuple(job[col] for col in job_info_columns) for job in job_info_batch]

        # Use execute_batch for better performance
        execute_batch(cursor, spot_insert_sql, spot_batch_data)
        execute_batch(cursor, job_info_insert_sql, job_info_batch_data)
        conn.commit()

        total_inserted += len(spot_batch_data)
        if total_inserted % 1000 == 0:
            print(f"  Inserted {total_inserted}/{count} jobs...")

    cursor.close()

    print(f"\nSuccessfully injected {total_inserted} test managed jobs!")
    print(f"Job IDs: {max_job_id + 1} - {max_job_id + total_inserted}")

    return total_inserted


def main():
    """Main function to inject test managed jobs into PostgreSQL."""
    # Database connection parameters
    db_params = {
        'host': 'localhost',
        'port': 5432,
        'database': 'skypilot',
        'user': 'skypilot',
        'password': 'skypilot'
    }

    count = 10000
    managed_job_id = 9  # Template job ID

    print("=" * 60)
    print(f"PostgreSQL Managed Job Injection Test")
    print("=" * 60)
    print(f"Database: {db_params['database']}@{db_params['host']}")
    print(f"Jobs to inject: {count}")
    print(f"Template job_id: {managed_job_id}")
    print("=" * 60)

    try:
        # Connect to PostgreSQL
        print("\nConnecting to PostgreSQL...")
        conn = psycopg2.connect(**db_params)
        print("Connected successfully!")

        # Inject jobs
        injected = inject_managed_jobs(conn, count, managed_job_id)

        # Close connection
        conn.close()

        print("\n" + "=" * 60)
        print("INJECTION COMPLETE!")
        print("=" * 60)
        print(f"Total jobs injected: {injected}")
        print("\nTo clean up these jobs, run:")
        print("  python cleanup_postgres_managed_jobs.py")

    except psycopg2.Error as e:
        print(f"\nPostgreSQL Error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
