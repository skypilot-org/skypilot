#!/usr/bin/env python3
"""
Cleanup script to remove test managed jobs.
"""

import os
import sqlite3
import sys

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def main():
    """Cleanup test managed jobs from database."""
    db_path = os.path.expanduser("~/.sky/spot_jobs.db")

    print("Cleaning up test managed jobs...")
    print("=" * 60)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Find test jobs - they have run_timestamp starting with 'sky-'
        # and were created by our test generator (look for recent ones with sky-cmd name)
        cursor.execute("""
            SELECT job_id, run_timestamp
            FROM spot
            WHERE run_timestamp LIKE 'sky-%'
            AND job_id > 1
            ORDER BY job_id
        """)
        jobs = cursor.fetchall()

        if not jobs:
            print("No test managed jobs found.")
            return

        print(f"Found {len(jobs)} test managed jobs")
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
