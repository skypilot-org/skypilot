#!/usr/bin/env python3
"""
Cleanup script to remove test cluster history entries.
"""

import os
import sqlite3
import sys

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def main():
    """Cleanup test cluster history from database."""
    db_path = os.path.expanduser("~/.sky/state.db")

    print("Cleaning up test cluster history...")
    print("=" * 60)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Find test cluster history (both recent and old)
        cursor.execute("""
            SELECT cluster_hash, name
            FROM cluster_history
            WHERE name LIKE 'test-cluster-recent-%' OR name LIKE 'test-cluster-old-%'
        """)
        entries = cursor.fetchall()

        if not entries:
            print("No test cluster history entries found.")
            return

        print(f"Found {len(entries)} test cluster history entries")

        # Delete them
        cluster_hashes = [entry[0] for entry in entries]
        placeholders = ', '.join(['?' for _ in cluster_hashes])
        cursor.execute(
            f"DELETE FROM cluster_history WHERE cluster_hash IN ({placeholders})",
            cluster_hashes)
        conn.commit()

        print(f"Successfully deleted {len(entries)} cluster history entries!")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"Cleanup failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
