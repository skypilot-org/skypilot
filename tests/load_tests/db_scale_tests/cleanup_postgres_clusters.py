#!/usr/bin/env python3
"""
Cleanup test clusters and cluster history from PostgreSQL database.
This script should be run from within the skypilot-api-server pod.
"""

try:
    import psycopg2
except ImportError:
    print("Error: psycopg2 not installed. Install with: pip install psycopg2-binary")
    exit(1)


def cleanup_clusters(conn):
    """Cleanup test clusters from PostgreSQL database."""
    cursor = conn.cursor()

    # Find test clusters - they have names starting with 'test-cluster-'
    cursor.execute("""
        SELECT name, cluster_hash
        FROM clusters
        WHERE name LIKE 'test-cluster-%'
        ORDER BY name
    """)
    clusters = cursor.fetchall()

    if not clusters:
        print("No test clusters found.")
        cursor.close()
        return 0, 0

    print(f"Found {len(clusters)} test clusters")
    cluster_names = [cluster[0] for cluster in clusters]

    # Delete from cluster_yaml table first
    cursor.execute(
        "DELETE FROM cluster_yaml WHERE cluster_name = ANY(%s)",
        (cluster_names,)
    )
    deleted_yaml = cursor.rowcount
    print(f"Deleted {deleted_yaml} entries from cluster_yaml table")

    # Delete from clusters table
    cursor.execute(
        "DELETE FROM clusters WHERE name = ANY(%s)",
        (cluster_names,)
    )
    deleted_clusters = cursor.rowcount

    conn.commit()
    cursor.close()

    print(f"Successfully deleted {deleted_clusters} clusters from clusters table")

    return deleted_clusters, deleted_yaml


def cleanup_cluster_history(conn):
    """Cleanup test cluster history from PostgreSQL database."""
    cursor = conn.cursor()

    # Find test cluster history - they have names starting with 'test-cluster-recent-' or 'test-cluster-old-'
    cursor.execute("""
        SELECT name, cluster_hash
        FROM cluster_history
        WHERE name LIKE 'test-cluster-recent-%'
           OR name LIKE 'test-cluster-old-%'
        ORDER BY name
    """)
    history_clusters = cursor.fetchall()

    if not history_clusters:
        print("No test cluster history found.")
        cursor.close()
        return 0

    print(f"Found {len(history_clusters)} test cluster history entries")
    cluster_names = [cluster[0] for cluster in history_clusters]

    # Delete from cluster_history table
    cursor.execute(
        "DELETE FROM cluster_history WHERE name = ANY(%s)",
        (cluster_names,)
    )
    deleted_history = cursor.rowcount

    conn.commit()
    cursor.close()

    print(f"Successfully deleted {deleted_history} entries from cluster_history table")

    return deleted_history


def main():
    """Main function to cleanup test clusters from PostgreSQL."""
    # Database connection parameters
    db_params = {
        'host': 'localhost',
        'port': 5432,
        'database': 'skypilot',
        'user': 'skypilot',
        'password': 'skypilot'
    }

    print("=" * 60)
    print("PostgreSQL Cluster Cleanup")
    print("=" * 60)
    print(f"Database: {db_params['database']}@{db_params['host']}")
    print("=" * 60)

    try:
        # Connect to PostgreSQL
        print("\nConnecting to PostgreSQL...")
        conn = psycopg2.connect(**db_params)
        print("Connected successfully!")

        # Cleanup clusters
        print("\nCleaning up test clusters...")
        deleted_clusters, deleted_yaml = cleanup_clusters(conn)

        # Cleanup cluster history
        print("\nCleaning up test cluster history...")
        deleted_history = cleanup_cluster_history(conn)

        # Close connection
        conn.close()

        print("\n" + "=" * 60)
        print("CLEANUP COMPLETE!")
        print("=" * 60)
        print(f"Total clusters deleted: {deleted_clusters}")
        print(f"Total cluster_yaml entries deleted: {deleted_yaml}")
        print(f"Total cluster history deleted: {deleted_history}")

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
