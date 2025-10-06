#!/usr/bin/env python3
"""
Inject test clusters and cluster history into PostgreSQL database for scale testing.
This script should be run from within the skypilot-api-server pod.
"""

import copy
import os
import pickle
import random
import time
import uuid

try:
    import psycopg2
    from psycopg2.extras import execute_batch
except ImportError:
    print("Error: psycopg2 not installed. Install with: pip install psycopg2-binary")
    exit(1)


def load_active_cluster_sample(conn, cluster_name='scale-test-active'):
    """Load an active cluster from PostgreSQL as a template."""
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM clusters WHERE name = %s", (cluster_name,))
    columns = [desc[0] for desc in cursor.description]
    row = cursor.fetchone()

    if not row:
        raise ValueError(
            f"Active cluster '{cluster_name}' not found in database. "
            f"Please create it first."
        )

    sample = dict(zip(columns, row))
    cursor.close()

    return sample, columns


def load_terminated_cluster_sample(conn, cluster_name='scale-test-terminated'):
    """Load a terminated cluster from cluster_history as a template."""
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM cluster_history WHERE name = %s", (cluster_name,))
    columns = [desc[0] for desc in cursor.description]
    row = cursor.fetchone()

    if not row:
        raise ValueError(
            f"Terminated cluster '{cluster_name}' not found in cluster_history. "
            f"Please create and terminate it first."
        )

    sample = dict(zip(columns, row))
    cursor.close()

    return sample, columns


def update_handle_cluster_name(handle_blob, new_cluster_name):
    """Update the cluster name in a pickled handle object."""
    try:
        handle = pickle.loads(handle_blob)
        # Update cluster_name if it exists
        if hasattr(handle, 'cluster_name'):
            handle.cluster_name = new_cluster_name
        # Re-pickle and return
        return pickle.dumps(handle)
    except Exception as e:
        # If unpickling fails, just return the original blob
        print(f"Warning: Failed to update handle cluster name: {e}")
        return handle_blob


def deep_copy_cluster(cluster_dict):
    """Deep copy a cluster dict, handling memoryview objects."""
    result = {}
    for key, value in cluster_dict.items():
        if isinstance(value, memoryview):
            # Convert memoryview to bytes
            result[key] = bytes(value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def generate_cluster_data(active_cluster_sample, count):
    """Generate test data for clusters table by cloning the sample cluster."""
    clusters = []
    current_time = time.time()

    for i in range(count):
        # Deep copy the sample cluster, handling memoryview objects
        cluster = deep_copy_cluster(active_cluster_sample)

        # Modify unique fields
        cluster['name'] = f"test-cluster-{i+1:04d}-{uuid.uuid4().hex[:8]}"
        cluster['cluster_hash'] = str(uuid.uuid4())
        cluster['launched_at'] = int(
            current_time - random.uniform(0, 7 * 24 * 3600))  # Last 7 days
        cluster['status_updated_at'] = int(
            current_time - random.uniform(0, 24 * 3600))  # Last day

        # Update handle with new cluster name if handle exists
        if cluster.get('handle'):
            cluster['handle'] = update_handle_cluster_name(
                cluster['handle'], cluster['name'])

        clusters.append(cluster)

    return clusters


def generate_cluster_history_data(terminated_cluster_sample, recent_count, old_count):
    """Generate cluster history data by cloning the terminated cluster sample."""
    history_clusters = []
    current_time = int(time.time())

    # Generate recent clusters (within 10 days)
    recent_min_days = 1 * 24 * 60 * 60  # 1 day ago
    recent_max_days = 9 * 24 * 60 * 60  # 9 days ago

    for i in range(recent_count):
        cluster = deep_copy_cluster(terminated_cluster_sample)

        # Modify unique fields
        cluster['name'] = f"test-cluster-recent-{i+1:04d}-{uuid.uuid4().hex[:8]}"
        cluster['cluster_hash'] = str(uuid.uuid4())

        # Random timestamp 1-9 days ago
        days_ago_seconds = random.randint(recent_min_days, recent_max_days)
        cluster['last_activity_time'] = current_time - days_ago_seconds
        cluster['launched_at'] = cluster['last_activity_time'] - random.randint(
            3600, 86400)

        history_clusters.append(cluster)

    # Generate older clusters (15-30 days ago)
    old_min_days = 15 * 24 * 60 * 60  # 15 days ago
    old_max_days = 30 * 24 * 60 * 60  # 30 days ago

    for i in range(old_count):
        cluster = deep_copy_cluster(terminated_cluster_sample)

        # Modify unique fields
        cluster['name'] = f"test-cluster-old-{i+1:04d}-{uuid.uuid4().hex[:8]}"
        cluster['cluster_hash'] = str(uuid.uuid4())

        # Random timestamp 15-30 days ago
        days_ago_seconds = random.randint(old_min_days, old_max_days)
        cluster['last_activity_time'] = current_time - days_ago_seconds
        cluster['launched_at'] = cluster['last_activity_time'] - random.randint(
            3600, 86400)

        history_clusters.append(cluster)

    return history_clusters


def inject_clusters(conn, count, active_cluster_name='scale-test-active'):
    """Inject test clusters into PostgreSQL database."""
    print(f"Injecting {count} test clusters...")

    # Load sample data
    print("Loading active cluster sample...")
    active_cluster_sample, cluster_columns = load_active_cluster_sample(
        conn, active_cluster_name
    )

    # Load cluster YAML for the sample
    print("Loading cluster YAML...")
    cursor = conn.cursor()
    cursor.execute("SELECT yaml FROM cluster_yaml WHERE cluster_name = %s", (active_cluster_name,))
    sample_yaml_row = cursor.fetchone()
    sample_yaml = sample_yaml_row[0] if sample_yaml_row else None

    if not sample_yaml:
        print(f"Warning: No cluster_yaml found for {active_cluster_name}. Cluster YAML will not be injected.")

    print(f"Generating {count} test clusters...")
    clusters = generate_cluster_data(active_cluster_sample, count)

    # Prepare insert statement for clusters table
    columns_str = ', '.join(cluster_columns)
    placeholders = ', '.join(['%s' for _ in cluster_columns])
    insert_sql = f"INSERT INTO clusters ({columns_str}) VALUES ({placeholders})"

    # Prepare insert statement for cluster_yaml table
    yaml_insert_sql = """
        INSERT INTO cluster_yaml (cluster_name, yaml)
        VALUES (%s, %s)
        ON CONFLICT (cluster_name) DO UPDATE SET yaml = EXCLUDED.yaml
    """

    # Insert in batches for performance
    batch_size = 100
    total_inserted = 0

    print(f"Inserting clusters in batches of {batch_size}...")
    for i in range(0, len(clusters), batch_size):
        batch = clusters[i:i + batch_size]
        batch_data = [tuple(cluster[col] for col in cluster_columns) for cluster in batch]

        execute_batch(cursor, insert_sql, batch_data)
        conn.commit()

        # Also insert cluster_yaml entries if we have sample YAML
        if sample_yaml:
            yaml_batch_data = [(cluster['name'], sample_yaml) for cluster in batch]
            execute_batch(cursor, yaml_insert_sql, yaml_batch_data)
            conn.commit()

        total_inserted += len(batch_data)
        if total_inserted % 500 == 0:
            print(f"  Inserted {total_inserted}/{count} clusters...")

    cursor.close()

    print(f"\nSuccessfully injected {total_inserted} test clusters!")
    if sample_yaml:
        print(f"Successfully injected {total_inserted} cluster_yaml entries!")

    return total_inserted


def inject_cluster_history(conn, recent_count, old_count,
                           terminated_cluster_name='scale-test-terminated'):
    """Inject test cluster history entries into PostgreSQL database."""
    total_count = recent_count + old_count
    print(f"\nInjecting {total_count} terminated cluster history entries...")
    print(f"  - {recent_count} recent clusters (within 10 days)")
    print(f"  - {old_count} older clusters (15-30 days ago)")

    # Load sample data
    print("Loading terminated cluster sample...")
    terminated_cluster_sample, history_columns = load_terminated_cluster_sample(
        conn, terminated_cluster_name
    )

    print(f"Generating {total_count} test cluster history entries...")
    history_clusters = generate_cluster_history_data(
        terminated_cluster_sample, recent_count, old_count
    )

    # Prepare insert statement
    columns_str = ', '.join(history_columns)
    placeholders = ', '.join(['%s' for _ in history_columns])
    insert_sql = f"INSERT INTO cluster_history ({columns_str}) VALUES ({placeholders})"

    # Insert in batches for performance
    batch_size = 100
    total_inserted = 0

    cursor = conn.cursor()

    print(f"Inserting cluster history in batches of {batch_size}...")
    for i in range(0, len(history_clusters), batch_size):
        batch = history_clusters[i:i + batch_size]
        batch_data = [tuple(cluster[col] for col in history_columns) for cluster in batch]

        execute_batch(cursor, insert_sql, batch_data)
        conn.commit()

        total_inserted += len(batch_data)
        if total_inserted % 1000 == 0:
            print(f"  Inserted {total_inserted}/{total_count} history entries...")

    cursor.close()

    print(f"\nSuccessfully injected {total_inserted} test cluster history entries!")

    return total_inserted


def main():
    """Main function to inject test clusters into PostgreSQL."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Inject test clusters into PostgreSQL database for scale testing'
    )
    parser.add_argument('--cluster-count', type=int, default=2000,
                        help='Number of active clusters to inject (default: 2000)')
    parser.add_argument('--recent-history', type=int, default=2000,
                        help='Number of recent cluster history entries (1-10 days old) (default: 2000)')
    parser.add_argument('--old-history', type=int, default=8000,
                        help='Number of old cluster history entries (15-30 days old) (default: 8000)')
    parser.add_argument('--active-cluster', type=str, default='scale-test-active',
                        help='Name of active cluster to use as template (default: scale-test-active)')
    parser.add_argument('--terminated-cluster', type=str, default='scale-test-terminated',
                        help='Name of terminated cluster to use as template (default: scale-test-terminated)')
    parser.add_argument('--host', type=str, default='localhost',
                        help='Database host (default: localhost)')
    parser.add_argument('--port', type=int, default=5432,
                        help='Database port (default: 5432)')
    parser.add_argument('--database', type=str, default='skypilot',
                        help='Database name (default: skypilot)')
    parser.add_argument('--user', type=str, default='skypilot',
                        help='Database user (default: skypilot)')
    parser.add_argument('--password', type=str, default='skypilot',
                        help='Database password (default: skypilot)')

    args = parser.parse_args()

    # Database connection parameters
    db_params = {
        'host': args.host,
        'port': args.port,
        'database': args.database,
        'user': args.user,
        'password': args.password
    }

    cluster_count = args.cluster_count
    recent_history_count = args.recent_history
    old_history_count = args.old_history
    active_cluster_name = args.active_cluster
    terminated_cluster_name = args.terminated_cluster

    print("=" * 60)
    print(f"PostgreSQL Cluster Injection Test")
    print("=" * 60)
    print(f"Database: {db_params['database']}@{db_params['host']}")
    print(f"Active clusters to inject: {cluster_count}")
    print(f"Recent history (1-10 days): {recent_history_count}")
    print(f"Old history (15-30 days): {old_history_count}")
    print(f"Template active cluster: {active_cluster_name}")
    print(f"Template terminated cluster: {terminated_cluster_name}")
    print("=" * 60)

    try:
        # Connect to PostgreSQL
        print("\nConnecting to PostgreSQL...")
        conn = psycopg2.connect(**db_params)
        print("Connected successfully!")

        # Inject active clusters
        clusters_injected = inject_clusters(conn, cluster_count, active_cluster_name)

        # Inject cluster history
        history_injected = inject_cluster_history(
            conn, recent_history_count, old_history_count, terminated_cluster_name
        )

        # Close connection
        conn.close()

        print("\n" + "=" * 60)
        print("INJECTION COMPLETE!")
        print("=" * 60)
        print(f"Total active clusters injected: {clusters_injected}")
        print(f"Total cluster history injected: {history_injected}")
        print(f"  - Recent (1-10 days): {recent_history_count}")
        print(f"  - Old (15-30 days): {old_history_count}")
        print("\nTo clean up these clusters, run:")
        print("  python cleanup_postgres_clusters.py")

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
