#!/usr/bin/env python3
"""
Cleanup test managed jobs from PostgreSQL database.
This script should be run from within the skypilot-api-server pod.
"""

try:
    import psycopg2
except ImportError:
    print("Error: psycopg2 not installed. Install with: pip install psycopg2-binary")
    exit(1)


def cleanup_managed_jobs(conn):
    """Cleanup test managed jobs from PostgreSQL database."""
    cursor = conn.cursor()

    # Find test jobs - they have run_timestamp starting with 'sky-'
    # and were created by our test generator
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
        cursor.close()
        return 0

    print(f"Found {len(jobs)} test managed jobs")
    job_ids = [job[0] for job in jobs]

    # Delete from both tables
    cursor.execute(
        "DELETE FROM spot WHERE job_id = ANY(%s)",
        (job_ids,)
    )
    deleted_spot = cursor.rowcount

    cursor.execute(
        "DELETE FROM job_info WHERE spot_job_id = ANY(%s)",
        (job_ids,)
    )
    deleted_job_info = cursor.rowcount

    conn.commit()
    cursor.close()

    print(f"Successfully deleted {deleted_spot} jobs from spot table")
    print(f"Successfully deleted {deleted_job_info} jobs from job_info table")

    return deleted_spot


def main():
    """Main function to cleanup test managed jobs from PostgreSQL."""
    # Database connection parameters
    db_params = {
        'host': 'localhost',
        'port': 5432,
        'database': 'skypilot',
        'user': 'skypilot',
        'password': 'skypilot'
    }

    print("=" * 60)
    print("PostgreSQL Managed Job Cleanup")
    print("=" * 60)
    print(f"Database: {db_params['database']}@{db_params['host']}")
    print("=" * 60)

    try:
        # Connect to PostgreSQL
        print("\nConnecting to PostgreSQL...")
        conn = psycopg2.connect(**db_params)
        print("Connected successfully!")

        # Cleanup jobs
        deleted = cleanup_managed_jobs(conn)

        # Close connection
        conn.close()

        print("\n" + "=" * 60)
        print("CLEANUP COMPLETE!")
        print("=" * 60)
        print(f"Total jobs deleted: {deleted}")

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
