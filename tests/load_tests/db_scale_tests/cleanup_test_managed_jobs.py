#!/usr/bin/env python3
"""
Cleanup script to remove test managed jobs.
"""

import argparse
import os
import sqlite3
import sys

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def main():
    """Cleanup test managed jobs from database."""
    parser = argparse.ArgumentParser(
        description='Cleanup test managed jobs injected after a specific job ID')
    parser.add_argument(
        '--managed-job-id',
        type=int,
        required=True,
        help='Job ID of the template managed job. All jobs with ID > this will be deleted.'
    )

    args = parser.parse_args()

    db_path = os.path.expanduser("~/.sky/spot_jobs.db")

    print(f"Cleaning up test managed jobs (job_id > {args.managed_job_id})...")
    print("=" * 60)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Find test jobs - all jobs with job_id greater than the template job
        cursor.execute("""
            SELECT job_id, run_timestamp
            FROM spot
            WHERE job_id > ?
            ORDER BY job_id
        """, (args.managed_job_id,))
        jobs = cursor.fetchall()

        if not jobs:
            print(f"No test managed jobs found with job_id > {args.managed_job_id}.")
            return

        print(f"Found {len(jobs)} test managed jobs to delete")
        print(f"Job ID range: {jobs[0][0]} - {jobs[-1][0]}")
        job_ids = [job[0] for job in jobs]

        # Delete from both tables
        placeholders = ', '.join(['?' for _ in job_ids])
        cursor.execute(f"DELETE FROM spot WHERE job_id IN ({placeholders})",
                       job_ids)
        cursor.execute(
            f"DELETE FROM job_info WHERE spot_job_id IN ({placeholders})",
            job_ids)
        conn.commit()

        print(f"Successfully deleted {len(jobs)} managed jobs!")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"Cleanup failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
