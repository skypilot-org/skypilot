#!/usr/bin/env python3
"""
Utilities for SkyPilot scale testing.

Provides TestScale class for injecting test data and measuring performance.
"""

import os
import sqlite3
import sys

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from sample_based_generator import SampleBasedGenerator

from sky import global_user_state
from sky.jobs import state as job_state


class TestScale:
    """Comprehensive scale tests for SkyPilot operations."""

    def initialize(self, active_cluster_name, terminated_cluster_name,
                   managed_job_id):
        """Initialize the test instance.

        Args:
            active_cluster_name: Name of a running cluster to use as template
            terminated_cluster_name: Name of a terminated cluster to use as template
            managed_job_id: Job ID of a managed job to use as template
        """
        self.db_path = os.path.expanduser("~/.sky/state.db")
        self.jobs_db_path = os.path.expanduser("~/.sky/spot_jobs.db")
        self.test_cluster_names = []
        self.test_cluster_hashes = []
        self.test_job_ids = []  # Track job IDs for cleanup
        self.generator = SampleBasedGenerator(
            active_cluster_name=active_cluster_name,
            terminated_cluster_name=terminated_cluster_name,
            managed_job_id=managed_job_id)

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

        # Clean up managed jobs
        if self.test_job_ids:
            try:
                jobs_conn = sqlite3.connect(self.jobs_db_path)
                jobs_cursor = jobs_conn.cursor()

                # Delete test jobs by job_id
                placeholders = ', '.join(['?' for _ in self.test_job_ids])
                jobs_cursor.execute(
                    f"DELETE FROM spot WHERE job_id IN ({placeholders})",
                    self.test_job_ids)
                jobs_cursor.execute(
                    f"DELETE FROM job_info WHERE spot_job_id IN ({placeholders})",
                    self.test_job_ids)
                jobs_conn.commit()
                print(f"Cleaned up {len(self.test_job_ids)} test managed jobs")

                jobs_cursor.close()
                jobs_conn.close()
            except Exception as e:
                print(f"Managed jobs cleanup failed: {e}")

    def inject_clusters(self, count: int = 2000):
        """Inject test clusters into database."""
        print(f"Injecting {count} test clusters...")

        # Generate test data from sample
        clusters = self.generator.generate_cluster_data(count)

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get column names from first cluster dict
            cluster_columns = list(clusters[0].keys())
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
                    # Convert dict to tuple in column order
                    row_data = tuple(cluster[col] for col in cluster_columns)
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

        # Generate test data from sample
        history_clusters = self.generator.generate_cluster_history_data(
            recent_count=recent_count, old_count=old_count)

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get column names from first cluster dict
            cluster_columns = list(history_clusters[0].keys())
            columns_str = ', '.join(cluster_columns)
            placeholders = ', '.join(['?' for _ in cluster_columns])

            insert_sql = f"INSERT INTO cluster_history ({columns_str}) VALUES ({placeholders})"

            batch_size = 100
            total_inserted = 0

            for i in range(0, len(history_clusters), batch_size):
                batch = history_clusters[i:i + batch_size]
                batch_data = []

                for cluster in batch:
                    # Convert dict to tuple in column order
                    row_data = tuple(cluster[col] for col in cluster_columns)
                    batch_data.append(row_data)
                    self.test_cluster_hashes.append(cluster['cluster_hash'])

                cursor.executemany(insert_sql, batch_data)
                conn.commit()
                total_inserted += len(batch_data)

            cursor.close()
            conn.close()
            return total_inserted

        except Exception as e:
            print(f"Failed to inject cluster history: {e}")
            return 0

    def inject_managed_jobs(self, count: int = 10000):
        """Inject test managed jobs into database."""
        print(f"Injecting {count} test managed jobs...")

        # Generate test data from sample
        spot_jobs, job_infos = self.generator.generate_managed_job_data(count)

        try:
            conn = sqlite3.connect(self.jobs_db_path)
            cursor = conn.cursor()

            # Get column names from first dict
            spot_columns = list(spot_jobs[0].keys())
            job_info_columns = list(job_infos[0].keys())

            spot_columns_str = ', '.join(spot_columns)
            spot_placeholders = ', '.join(['?' for _ in spot_columns])
            job_info_columns_str = ', '.join(job_info_columns)
            job_info_placeholders = ', '.join(['?' for _ in job_info_columns])

            spot_insert_sql = f"INSERT INTO spot ({spot_columns_str}) VALUES ({spot_placeholders})"
            job_info_insert_sql = f"INSERT INTO job_info ({job_info_columns_str}) VALUES ({job_info_placeholders})"

            # Insert in batches for performance
            batch_size = 100
            total_inserted = 0

            for i in range(0, len(spot_jobs), batch_size):
                spot_batch = spot_jobs[i:i + batch_size]
                job_info_batch = job_infos[i:i + batch_size]

                # Convert dicts to tuples in column order
                spot_batch_data = []
                job_info_batch_data = []

                for spot_job in spot_batch:
                    row_data = tuple(spot_job[col] for col in spot_columns)
                    spot_batch_data.append(row_data)

                for job_info in job_info_batch:
                    row_data = tuple(job_info[col] for col in job_info_columns)
                    job_info_batch_data.append(row_data)

                cursor.executemany(spot_insert_sql, spot_batch_data)
                cursor.executemany(job_info_insert_sql, job_info_batch_data)
                conn.commit()

                total_inserted += len(spot_batch_data)

            # Store job IDs for cleanup
            self.test_job_ids.extend([job['job_id'] for job in spot_jobs])

            cursor.close()
            conn.close()
            return total_inserted

        except Exception as e:
            print(f"Failed to inject managed jobs: {e}")
            return 0

