#!/usr/bin/env python3
"""
Comprehensive scale tests for SkyPilot.

Tests cluster operations and cluster history operations with large datasets.
"""

import os
import sqlite3
import subprocess
import sys
import time

import pytest

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from sample_based_generator import SampleBasedGenerator

from sky import global_user_state
from sky.jobs import state as job_state


class TestScale:
    """Comprehensive scale tests for SkyPilot operations."""

    def setup_method(self, active_cluster_name, terminated_cluster_name,
                     managed_job_id):
        """Setup for each test method.

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

    # Managed Jobs Tests
    def test_get_managed_jobs_performance(self, performance_logger,
                                          backup_databases):
        """Test get_managed_jobs performance with 10,000 managed jobs."""
        # Inject test data
        count = self.inject_managed_jobs(10000)
        assert count == 10000, f"Expected to inject 10000 managed jobs, got {count}"

        # Benchmark get_managed_jobs
        start_time = time.time()
        jobs = job_state.get_managed_jobs()
        duration = time.time() - start_time

        # Log performance
        performance_logger("test_scale", "get_managed_jobs", len(jobs),
                           duration)

        # Assertions
        assert len(
            jobs) >= 10000, f"Expected at least 10000 jobs, got {len(jobs)}"
        assert duration < 0.3, f"get_managed_jobs took too long: {duration:.3f}s"

        print(f"get_managed_jobs: {len(jobs)} jobs in {duration:.3f}s")

    def test_sky_jobs_queue_performance(self, performance_logger,
                                        backup_databases):
        """Test sky jobs queue command performance with 10,000 managed jobs."""
        # Inject test data
        count = self.inject_managed_jobs(10000)
        assert count == 10000, f"Expected to inject 10000 managed jobs, got {count}"

        # Benchmark sky jobs queue
        start_time = time.time()
        try:
            result = subprocess.run(['sky', 'jobs', 'queue'],
                                    capture_output=True,
                                    text=True,
                                    timeout=30)
            duration = time.time() - start_time

            # Log performance
            performance_logger("test_scale", "sky_jobs_queue", 10000, duration)

            # Assertions
            assert result.returncode == 0, f"sky jobs queue failed: {result.stderr}"
            assert duration < 7.0, f"sky jobs queue took too long: {duration:.3f}s"

            # Count jobs in output (look for sky-cmd jobs which are our test jobs)
            output_lines = result.stdout.split('\n')
            job_lines = [
                line for line in output_lines
                if 'sky-cmd' in line and 'RUNNING' in line
            ]

            print(
                f"sky jobs queue: {len(job_lines)} test jobs shown in {duration:.3f}s"
            )
            assert len(
                job_lines) > 0, "No test jobs found in sky jobs queue output"

        except subprocess.TimeoutExpired:
            pytest.fail("sky jobs queue command timed out after 30 seconds")
        except Exception as e:
            pytest.fail(f"sky jobs queue command failed: {e}")
