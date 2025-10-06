#!/usr/bin/env python3
"""
Cleanup script to remove test clusters.
"""

import os
import sqlite3
import sys

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def main():
    """Cleanup test clusters from database."""
    db_path = os.path.expanduser("~/.sky/state.db")

    print("Cleaning up test clusters...")
    print("=" * 60)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Find test clusters
        cursor.execute(
            "SELECT name FROM clusters WHERE name LIKE 'test-cluster-%'")
        cluster_names = [row[0] for row in cursor.fetchall()]

        if not cluster_names:
            print("No test clusters found.")
            return

        print(f"Found {len(cluster_names)} test clusters:")
        for name in cluster_names:
            print(f"  - {name}")

        # Delete from cluster_yaml table first
        placeholders = ', '.join(['?' for _ in cluster_names])
        cursor.execute(f"DELETE FROM cluster_yaml WHERE cluster_name IN ({placeholders})",
                       cluster_names)
        yaml_deleted = cursor.rowcount

        # Delete from clusters table
        cursor.execute(f"DELETE FROM clusters WHERE name IN ({placeholders})",
                       cluster_names)
        clusters_deleted = cursor.rowcount

        conn.commit()

        print(f"\nSuccessfully deleted {clusters_deleted} test clusters!")
        print(f"Successfully deleted {yaml_deleted} cluster_yaml entries!")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"Cleanup failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
