#!/usr/bin/env python3
"""
Utilities for SkyPilot scale testing.

Provides TestScale class for injecting test data and measuring performance.
"""

import os
import sqlite3
import sys
from urllib.parse import parse_qs
from urllib.parse import unquote
from urllib.parse import urlparse

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import psycopg2
from psycopg2.extras import execute_batch
from sample_based_generator import SampleBasedGenerator

from sky import global_user_state
from sky.jobs import state as job_state


def parse_sql_url(sql_url):
    """Parse a PostgreSQL connection URL and return connection parameters.

    Args:
        sql_url: Connection URL in format
                postgresql://user:password@host:port/database?sslmode=require

    Returns:
        dict with keys: host, port, database, user, password, sslmode (if present)
    """
    parsed = urlparse(sql_url)

    params = {
        'host': parsed.hostname or 'localhost',
        'port': parsed.port or 5432,
        'database': parsed.path.lstrip('/') if parsed.path else 'skypilot',
        'user': unquote(parsed.username) if parsed.username else 'skypilot',
        'password': unquote(parsed.password) if parsed.password else ''
    }

    # Parse query parameters (e.g., sslmode=require)
    if parsed.query:
        query_params = parse_qs(parsed.query)
        for key, value_list in query_params.items():
            if value_list:
                params[key] = value_list[0]

    return params


class TestScale:
    """Comprehensive scale tests for SkyPilot operations."""

    def initialize(self,
                   active_cluster_name,
                   terminated_cluster_name,
                   managed_job_id,
                   sql_url=None):
        """Initialize the test instance.

        Args:
            active_cluster_name: Name of a running cluster to use as template
            terminated_cluster_name: Name of a terminated cluster to use as template
            managed_job_id: Job ID of a managed job to use as template
            sql_url: Optional PostgreSQL connection URL. If None, uses SQLite.
        """
        self.sql_url = sql_url
        self.use_postgres = sql_url is not None

        if self.use_postgres:
            self.db_params = parse_sql_url(sql_url)
            self.jobs_db_params = self.db_params.copy()
            print(f"Using PostgreSQL: {self.db_params['database']}@"
                  f"{self.db_params['host']}")
        else:
            self.db_path = os.path.expanduser("~/.sky/state.db")
            self.jobs_db_path = os.path.expanduser("~/.sky/spot_jobs.db")
            print(f"Using SQLite: {self.db_path}")

        self.test_cluster_names = []
        self.test_cluster_hashes = []
        self.test_job_ids = []  # Track job IDs for cleanup
        self.generator = SampleBasedGenerator(
            active_cluster_name=active_cluster_name,
            terminated_cluster_name=terminated_cluster_name,
            managed_job_id=managed_job_id,
            db_connection_getter=self._get_connection
            if self.use_postgres else None,
            format_sql=self._format_sql if self.use_postgres else None)

    def _get_connection(self, for_jobs=False):
        """Get a database connection (SQLite or PostgreSQL)."""
        if self.use_postgres:
            params = self.jobs_db_params if for_jobs else self.db_params
            return psycopg2.connect(**params)
        else:
            db_path = self.jobs_db_path if for_jobs else self.db_path
            return sqlite3.connect(db_path)

    def _format_sql(self, sql):
        """Format SQL query with correct placeholders for current database.

        Converts '?' placeholders to '%s' for PostgreSQL, leaves as-is for SQLite.
        """
        if self.use_postgres:
            return sql.replace('?', '%s')
        return sql

    def _execute_batch(self, cursor, sql, batch_data, page_size=100):
        """Execute batch insert/update with optimal method for database."""
        if self.use_postgres:
            execute_batch(cursor, sql, batch_data, page_size=page_size)
        else:
            cursor.executemany(sql, batch_data)

    def _get_integrity_error_class(self):
        """Get the IntegrityError exception class for current database."""
        if self.use_postgres:
            return psycopg2.IntegrityError
        return sqlite3.IntegrityError

    def _get_excluded_keyword(self):
        """Get the EXCLUDED keyword for ON CONFLICT clauses."""
        return 'EXCLUDED' if self.use_postgres else 'excluded'

    def teardown_method(self):
        """Cleanup after each test method."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Clean up active clusters
        if self.test_cluster_names:
            try:
                placeholders = ', '.join(['?' for _ in self.test_cluster_names])
                sql = self._format_sql(
                    f"DELETE FROM cluster_yaml WHERE cluster_name IN "
                    f"({placeholders})")
                cursor.execute(sql, self.test_cluster_names)
                yaml_deleted = cursor.rowcount

                sql = self._format_sql(
                    f"DELETE FROM clusters WHERE name IN ({placeholders})")
                cursor.execute(sql, self.test_cluster_names)
                clusters_deleted = cursor.rowcount

                conn.commit()
                print(f"Cleaned up {clusters_deleted} test clusters")
                print(f"Cleaned up {yaml_deleted} cluster_yaml entries")
            except Exception as e:
                print(f"Active clusters cleanup failed: {e}")

        # Clean up cluster history entries
        if self.test_cluster_hashes:
            try:
                placeholders = ', '.join(
                    ['?' for _ in self.test_cluster_hashes])
                sql = self._format_sql(
                    f"DELETE FROM cluster_history WHERE cluster_hash IN "
                    f"({placeholders})")
                cursor.execute(sql, self.test_cluster_hashes)
                conn.commit()
                print(
                    f"Cleaned up {len(self.test_cluster_hashes)} test cluster "
                    "history entries")
            except Exception as e:
                print(f"Cluster history cleanup failed: {e}")

        cursor.close()
        conn.close()

        # Clean up managed jobs
        if self.test_job_ids:
            try:
                jobs_conn = self._get_connection(for_jobs=True)
                jobs_cursor = jobs_conn.cursor()

                # Delete test jobs by job_id
                placeholders = ', '.join(['?' for _ in self.test_job_ids])
                sql = self._format_sql(
                    f"DELETE FROM spot WHERE job_id IN ({placeholders})")
                jobs_cursor.execute(sql, self.test_job_ids)
                sql = self._format_sql(
                    f"DELETE FROM job_info WHERE spot_job_id IN "
                    f"({placeholders})")
                jobs_cursor.execute(sql, self.test_job_ids)
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
            conn = self._get_connection()
            cursor = conn.cursor()

            # Load cluster YAML for the sample cluster
            sql = self._format_sql(
                "SELECT yaml FROM cluster_yaml WHERE cluster_name = ?")
            cursor.execute(sql, (self.generator.active_cluster_name,))
            sample_yaml_row = cursor.fetchone()
            sample_yaml = sample_yaml_row[0] if sample_yaml_row else None

            if not sample_yaml:
                print(f"Warning: No cluster_yaml found for "
                      f"{self.generator.active_cluster_name}. "
                      "Cluster YAML will not be injected.")

            # Get column names from first cluster dict
            cluster_columns = list(clusters[0].keys())
            columns_str = ', '.join(cluster_columns)
            placeholders = ', '.join(['?' for _ in cluster_columns])

            insert_sql = self._format_sql(
                f"INSERT INTO clusters ({columns_str}) VALUES "
                f"({placeholders})")

            # Prepare cluster_yaml insert
            excluded = self._get_excluded_keyword()
            yaml_insert_sql = self._format_sql(f"""
                INSERT INTO cluster_yaml (cluster_name, yaml)
                VALUES (?, ?)
                ON CONFLICT(cluster_name) DO UPDATE SET yaml = {excluded}.yaml
            """)

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

                self._execute_batch(cursor, insert_sql, batch_data, batch_size)
                conn.commit()

                # Also insert cluster_yaml entries if we have sample YAML
                if sample_yaml:
                    yaml_batch_data = [
                        (cluster['name'], sample_yaml) for cluster in batch
                    ]
                    self._execute_batch(cursor, yaml_insert_sql,
                                        yaml_batch_data, batch_size)
                    conn.commit()

                total_inserted += len(batch_data)

            # Store names for cleanup
            self.test_cluster_names.extend(
                [cluster['name'] for cluster in clusters])

            print(f"Injected {total_inserted} clusters")
            if sample_yaml:
                print(f"Injected {total_inserted} cluster_yaml entries")

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
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get column names from first cluster dict
            cluster_columns = list(history_clusters[0].keys())
            columns_str = ', '.join(cluster_columns)
            placeholders = ', '.join(['?' for _ in cluster_columns])

            insert_sql = self._format_sql(
                f"INSERT INTO cluster_history ({columns_str}) VALUES "
                f"({placeholders})")

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

                self._execute_batch(cursor, insert_sql, batch_data, batch_size)
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
        spot_jobs, job_infos = (self.generator.generate_managed_job_data(count))

        try:
            conn = self._get_connection(for_jobs=True)
            cursor = conn.cursor()

            # Get column names from first dict
            spot_columns = list(spot_jobs[0].keys())
            job_info_columns = list(job_infos[0].keys())

            spot_columns_str = ', '.join(spot_columns)
            spot_placeholders = ', '.join(['?' for _ in spot_columns])
            job_info_columns_str = ', '.join(job_info_columns)
            job_info_placeholders = ', '.join(['?' for _ in job_info_columns])

            spot_insert_sql = self._format_sql(
                f"INSERT INTO spot ({spot_columns_str}) VALUES "
                f"({spot_placeholders})")
            job_info_insert_sql = self._format_sql(
                f"INSERT INTO job_info ({job_info_columns_str}) VALUES "
                f"({job_info_placeholders})")

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

                self._execute_batch(cursor, spot_insert_sql, spot_batch_data,
                                    batch_size)
                self._execute_batch(cursor, job_info_insert_sql,
                                    job_info_batch_data, batch_size)
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
