#!/usr/bin/env python3
"""
Inject a minimal number of test clusters to reproduce the YAML not found error.
"""

import os
import pickle
import sqlite3
import sys
import time

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from sky.utils import common_utils


def main():
    """Inject 2 test clusters to reproduce YAML issue."""
    db_path = os.path.expanduser("~/.sky/state.db")

    print("Injecting minimal test clusters to reproduce YAML error...")
    print("=" * 60)

    # First check if we have a real cluster to use as template
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM clusters LIMIT 1")
    sample = cursor.fetchone()

    if not sample:
        print("ERROR: No clusters found in database to use as template.")
        print("Please create a cluster first with:")
        print("  sky launch --infra k8s -c sample-cluster -y \"echo test\"")
        conn.close()
        return

    # Convert sample to dict
    sample_dict = dict(sample)
    print(f"Using cluster '{sample_dict['name']}' as template")

    # Deserialize the handle to modify it
    original_handle = pickle.loads(sample_dict['handle'])
    print(f"Original handle cluster_yaml: {original_handle.cluster_yaml}")

    # Create 2 test clusters with missing YAML
    test_clusters = []

    for i in range(1, 3):
        cluster_name = f'test-yaml-missing-{i}'

        # Clone and modify the handle
        handle = pickle.loads(sample_dict['handle'])
        handle._cluster_name = cluster_name

        # Set cluster_yaml path to a non-existent file
        # This simulates the issue where the path is stored but file doesn't exist
        yaml_path = os.path.expanduser(f'~/.sky/generated/{cluster_name}.yml')
        handle._cluster_yaml = yaml_path

        # Serialize the modified handle
        handle_bytes = pickle.dumps(handle)

        cluster = {
            'name': cluster_name,
            'launched_at': int(time.time()),
            'handle': handle_bytes,
            'last_use': sample_dict['last_use'],
            'status': 'UP',
            'autostop': -1,
            'to_down': 0,
            'metadata': '{}',
            'owner': sample_dict.get('owner'),
            'cluster_hash': sample_dict.get('cluster_hash'),
            'storage_mounts_metadata':
                sample_dict.get('storage_mounts_metadata'),
            'cluster_ever_up': 1,
            'status_updated_at': int(time.time()),
            'config_hash': sample_dict.get('config_hash'),
            'user_hash': common_utils.get_user_hash(),
            'workspace': sample_dict.get('workspace', 'default'),
            'last_creation_yaml': sample_dict.get('last_creation_yaml'),
            'last_creation_command': sample_dict.get('last_creation_command'),
            'is_managed': 0,
            'provision_log_path': sample_dict.get('provision_log_path'),
            'skylet_ssh_tunnel_metadata':
                sample_dict.get('skylet_ssh_tunnel_metadata'),
        }

        test_clusters.append(cluster)
        print(f"  Created test cluster: {cluster_name}")
        print(f"    cluster_yaml path in handle: {yaml_path}")
        print(f"    File exists: {os.path.exists(yaml_path)}")
        print(f"    DB entry exists: <will check after insert>")

    # Insert clusters into database
    columns = list(test_clusters[0].keys())
    columns_str = ', '.join(columns)
    placeholders = ', '.join(['?' for _ in columns])
    insert_sql = f"INSERT INTO clusters ({columns_str}) VALUES ({placeholders})"

    for cluster in test_clusters:
        row_data = tuple(cluster[col] for col in columns)
        cursor.execute(insert_sql, row_data)

    conn.commit()

    # Check if cluster_yaml entries exist
    print("\nChecking cluster_yaml table:")
    for i in range(1, 3):
        cluster_name = f'test-yaml-missing-{i}'
        cursor.execute("SELECT * FROM cluster_yaml WHERE cluster_name = ?",
                       (cluster_name,))
        result = cursor.fetchone()
        print(f"  {cluster_name}: {'FOUND' if result else 'NOT FOUND'}")

    cursor.close()
    conn.close()

    print("\n" + "=" * 60)
    print("Test clusters injected successfully!")
    print("\nTo reproduce the error, run:")
    print("  sky status")
    print("\nExpected error:")
    print(
        "  ValueError: Cluster yaml ~/.sky/generated/test-yaml-missing-X.yml not found."
    )
    print("\nTo clean up:")
    print(
        "  sqlite3 ~/.sky/state.db \"DELETE FROM clusters WHERE name LIKE 'test-yaml-missing-%';\""
    )


if __name__ == "__main__":
    main()
