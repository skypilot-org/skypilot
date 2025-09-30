#!/usr/bin/env python3
"""
Comprehensive scale tests for SkyPilot.

Tests cluster operations and cluster history operations with large datasets.
"""

import os
import pickle
import random
import sqlite3
import subprocess
import sys
import time

import pytest

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from schema_generator import SchemaBasedGenerator

from sky import global_user_state


class TestScale:
    """Comprehensive scale tests for SkyPilot operations."""

    def setup_method(self):
        """Setup for each test method."""
        self.db_path = os.path.expanduser("~/.sky/state.db")
        self.test_cluster_names = []
        self.test_cluster_hashes = []
        self.generator = SchemaBasedGenerator()

    def teardown_method(self):
        """Cleanup after each test method."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Clean up active clusters
        if self.test_cluster_names:
            try:
                placeholders = ', '.join(['?' for _ in self.test_cluster_names])
                cursor.execute(
                    f"DELETE FROM clusters WHERE name IN ({placeholders})",
                    self.test_cluster_names)
                conn.commit()
                print(
                    f"Cleaned up {len(self.test_cluster_names)} test clusters")
            except Exception as e:
                print(f"Active clusters cleanup failed: {e}")

        # Clean up cluster history entries
        if self.test_cluster_hashes:
            try:
                placeholders = ', '.join(
                    ['?' for _ in self.test_cluster_hashes])
                cursor.execute(
                    f"DELETE FROM cluster_history WHERE cluster_hash IN ({placeholders})",
                    self.test_cluster_hashes)
                conn.commit()
                print(
                    f"Cleaned up {len(self.test_cluster_hashes)} test cluster history entries"
                )
            except Exception as e:
                print(f"Cluster history cleanup failed: {e}")

        cursor.close()
        conn.close()

    def inject_clusters(self, count: int = 2000):
        """Inject test clusters into database."""
        print(f"Injecting {count} test clusters...")

        # Generate test data using schema
        clusters = self.generator.generate_cluster_data(count)

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get column names from schema
            cluster_columns = [col[0] for col in self.generator.cluster_schema]
            columns_str = ', '.join(cluster_columns)
            placeholders = ', '.join(['?' for _ in cluster_columns])

            insert_sql = f"INSERT INTO clusters ({columns_str}) VALUES ({placeholders})"

            # Insert in batches for performance
            batch_size = 100
            total_inserted = 0

            for i in range(0, len(clusters), batch_size):
                batch = clusters[i:i + batch_size]
                batch_data = []

                for cluster in batch:
                    # Convert dict to tuple in column order, ensuring no None values
                    row_data = tuple(
                        cluster[col] if cluster[col] is not None else ""
                        for col in cluster_columns)
                    batch_data.append(row_data)

                cursor.executemany(insert_sql, batch_data)
                conn.commit()

                total_inserted += len(batch_data)

            # Store names for cleanup
            self.test_cluster_names.extend(
                [cluster['name'] for cluster in clusters])

            cursor.close()
            conn.close()
            return total_inserted

        except Exception as e:
            print(f"Failed to inject clusters: {e}")
            return 0

    def inject_cluster_history(self,
                               recent_count: int = 2000,
                               old_count: int = 8000):
        """Inject test cluster history entries into database.

        Args:
            recent_count: Number of clusters terminated within 10 days
            old_count: Number of clusters terminated 15-30 days ago
        """
        total_count = recent_count + old_count
        print(f"Injecting {total_count} terminated cluster history entries...")
        print(f"  - {recent_count} recent clusters (within 10 days)")
        print(f"  - {old_count} older clusters (15-30 days ago)")

        # Use minimal valid pickle data to avoid memory issues
        simple_resources_blob = pickle.dumps({
            'infra': 'kubernetes',
            'disk_size': 256
        })
        simple_handle_blob = pickle.dumps({
            'cluster_id': 'test-cluster',
            'head_ip': '10.0.0.1'
        })
        simple_usage_blob = pickle.dumps([])  # Empty usage intervals

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            current_time = int(time.time())

            insert_sql = """INSERT INTO cluster_history
                (cluster_hash, name, num_nodes, requested_resources, launched_resources,
                 usage_intervals, user_hash, last_creation_yaml, last_creation_command,
                 workspace, provision_log_path, last_activity_time, launched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

            batch_size = 100
            total_inserted = 0

            # First, inject recent clusters (within 10 days)
            print(f"  Injecting {recent_count} recent clusters...")
            recent_min_days = 1 * 24 * 60 * 60  # 1 day ago
            recent_max_days = 9 * 24 * 60 * 60  # 9 days ago (within 10 day range)

            for i in range(0, recent_count, batch_size):
                batch_data = []
                for j in range(min(batch_size, recent_count - i)):
                    cluster_hash = f"test-recent-{i+j:06d}-{random.randint(1000, 9999)}"
                    cluster_name = f"test-cluster-recent-{i+j:06d}"

                    # Random timestamp 1-9 days ago
                    days_ago_seconds = random.randint(recent_min_days,
                                                      recent_max_days)
                    last_activity = current_time - days_ago_seconds
                    launched_at = last_activity - random.randint(3600, 86400)

                    row_data = (
                        cluster_hash, cluster_name, 1, simple_resources_blob,
                        simple_handle_blob, simple_usage_blob, "5a58ba5d",
                        f"name: {cluster_name}\nresources:\n  infra: kubernetes\n  disk_size: 256\nnum_nodes: 1",
                        "sky launch --infra k8s -y", "default",
                        f"/tmp/{cluster_name}/provision.log", last_activity,
                        launched_at)

                    batch_data.append(row_data)
                    self.test_cluster_hashes.append(cluster_hash)

                cursor.executemany(insert_sql, batch_data)
                conn.commit()
                total_inserted += len(batch_data)

            # Then, inject older clusters (15-30 days ago, outside 10 day range)
            print(f"  Injecting {old_count} older clusters...")
            old_min_days = 15 * 24 * 60 * 60  # 15 days ago
            old_max_days = 30 * 24 * 60 * 60  # 30 days ago

            for i in range(0, old_count, batch_size):
                batch_data = []
                for j in range(min(batch_size, old_count - i)):
                    cluster_hash = f"test-old-{i+j:06d}-{random.randint(1000, 9999)}"
                    cluster_name = f"test-cluster-old-{i+j:06d}"

                    # Random timestamp 15-30 days ago
                    days_ago_seconds = random.randint(old_min_days,
                                                      old_max_days)
                    last_activity = current_time - days_ago_seconds
                    launched_at = last_activity - random.randint(3600, 86400)

                    row_data = (
                        cluster_hash, cluster_name, 1, simple_resources_blob,
                        simple_handle_blob, simple_usage_blob, "5a58ba5d",
                        f"name: {cluster_name}\nresources:\n  infra: kubernetes\n  disk_size: 256\nnum_nodes: 1",
                        "sky launch --infra k8s -y", "default",
                        f"/tmp/{cluster_name}/provision.log", last_activity,
                        launched_at)

                    batch_data.append(row_data)
                    self.test_cluster_hashes.append(cluster_hash)

                cursor.executemany(insert_sql, batch_data)
                conn.commit()
                total_inserted += len(batch_data)

            cursor.close()
            conn.close()
            return total_inserted

        except Exception as e:
            print(f"Failed to inject cluster history: {e}")
            return 0

    # Active Cluster Tests
    def test_get_clusters_performance(self, performance_logger,
                                      backup_databases):
        """Test get_clusters performance with 2000 active clusters."""
        # Inject test data
        count = self.inject_clusters(2000)
        assert count == 2000, f"Expected to inject 2000 clusters, got {count}"

        # Benchmark get_clusters
        start_time = time.time()
        clusters = global_user_state.get_clusters(
            exclude_managed_clusters=False, summary_response=False)
        duration = time.time() - start_time

        # Log performance
        performance_logger("test_scale", "get_clusters", len(clusters),
                           duration)

        # Assertions
        assert len(
            clusters
        ) >= 2000, f"Expected at least 2000 clusters, got {len(clusters)}"
        assert duration < 0.3, f"get_clusters took too long: {duration:.3f}s"

        print(f"get_clusters: {len(clusters)} clusters in {duration:.3f}s")

    def test_sky_status_performance(self, performance_logger, backup_databases):
        """Test sky status command performance with 2000 active clusters."""
        # Inject test data
        count = self.inject_clusters(2000)
        assert count == 2000, f"Expected to inject 2000 clusters, got {count}"

        # Benchmark sky status
        start_time = time.time()
        try:
            result = subprocess.run(['sky', 'status'],
                                    capture_output=True,
                                    text=True,
                                    timeout=30)
            duration = time.time() - start_time

            # Log performance
            performance_logger("test_scale", "sky_status", 2000, duration)

            # Assertions
            assert result.returncode == 0, f"sky status failed: {result.stderr}"
            assert duration < 5.0, f"sky status took too long: {duration:.3f}s"

            # Count clusters in output
            output_lines = result.stdout.split('\n')
            cluster_lines = [
                line for line in output_lines if 'test-cluster-' in line
            ]

            print(
                f"sky status: {len(cluster_lines)} test clusters shown in {duration:.3f}s"
            )
            assert len(cluster_lines
                      ) > 0, "No test clusters found in sky status output"

        except subprocess.TimeoutExpired:
            pytest.fail("sky status command timed out after 30 seconds")
        except Exception as e:
            pytest.fail(f"sky status command failed: {e}")

    # Cluster History Tests
    def test_get_clusters_from_history_10_days(self, performance_logger,
                                               backup_databases):
        """Test get_clusters_from_history performance with days=10 (recent history only)."""
        # Inject active clusters first, then cluster history
        active_count = self.inject_clusters(2000)
        history_count = self.inject_cluster_history(recent_count=2000,
                                                    old_count=8000)
        assert active_count == 2000, f"Expected to inject 2000 active clusters, got {active_count}"
        assert history_count == 10000, f"Expected to inject 10000 cluster history entries, got {history_count}"

        # Benchmark get_clusters_from_history with days=10
        start_time = time.time()
        history_clusters = global_user_state.get_clusters_from_history(days=10)
        duration = time.time() - start_time

        # Log performance
        performance_logger("test_scale", "get_clusters_from_history_10_days",
                           len(history_clusters), duration)

        # Assertions - should return ~2000 recent clusters
        print(
            f"get_clusters_from_history(days=10): {len(history_clusters)} clusters in {duration:.3f}s"
        )
        assert len(
            history_clusters
        ) >= 2000, f"Expected at least 2000 clusters, got {len(history_clusters)}"
        assert duration < 0.3, f"get_clusters_from_history took too long: {duration:.3f}s"

    def test_get_clusters_from_history_30_days(self, performance_logger,
                                               backup_databases):
        """Test get_clusters_from_history performance with days=30 (all terminated clusters)."""
        # Inject active clusters first, then cluster history
        active_count = self.inject_clusters(2000)
        history_count = self.inject_cluster_history(recent_count=2000,
                                                    old_count=8000)
        assert active_count == 2000, f"Expected to inject 2000 active clusters, got {active_count}"
        assert history_count == 10000, f"Expected to inject 10000 cluster history entries, got {history_count}"

        # Benchmark get_clusters_from_history with days=30
        start_time = time.time()
        history_clusters = global_user_state.get_clusters_from_history(days=30)
        duration = time.time() - start_time

        # Log performance
        performance_logger("test_scale", "get_clusters_from_history_30_days",
                           len(history_clusters), duration)

        # Assertions - should return all terminated clusters (2000 recent + 8000 old)
        print(
            f"get_clusters_from_history(days=30): {len(history_clusters)} clusters in {duration:.3f}s"
        )
        assert len(
            history_clusters
        ) >= 10000, f"Expected at least 10000 clusters, got {len(history_clusters)}"
        assert duration < 0.3, f"get_clusters_from_history took too long: {duration:.3f}s"
