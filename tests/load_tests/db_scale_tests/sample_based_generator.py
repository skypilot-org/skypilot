#!/usr/bin/env python3
"""
Sample-based test data generator for SkyPilot scale testing.

This module reads existing database entries as templates and generates
realistic test data by cloning and modifying these samples.
"""

import copy
import os
import pickle
import random
import sqlite3
import time
from typing import Any, Dict, List, Tuple
import uuid


class SampleBasedGenerator:
    """Generates test data based on sample database entries."""

    def __init__(self, active_cluster_name: str, terminated_cluster_name: str,
                 managed_job_id: int):
        """Initialize the generator by loading sample entries from database.

        Args:
            active_cluster_name: Name of a running cluster to use as template
            terminated_cluster_name: Name of a terminated cluster to use as template
            managed_job_id: Job ID of a managed job to use as template
        """
        self.active_cluster_name = active_cluster_name
        self.terminated_cluster_name = terminated_cluster_name
        self.managed_job_id = managed_job_id

        # Cache for lazy-loaded samples
        self._active_cluster_sample = None
        self._terminated_cluster_sample = None
        self._managed_job_sample = None

    @property
    def active_cluster_sample(self):
        """Lazy load active cluster sample."""
        if self._active_cluster_sample is None:
            self._active_cluster_sample = self._load_active_cluster_sample()
        return self._active_cluster_sample

    @property
    def terminated_cluster_sample(self):
        """Lazy load terminated cluster sample."""
        if self._terminated_cluster_sample is None:
            self._terminated_cluster_sample = self._load_terminated_cluster_sample(
            )
        return self._terminated_cluster_sample

    @property
    def managed_job_sample(self):
        """Lazy load managed job sample."""
        if self._managed_job_sample is None:
            self._managed_job_sample = self._load_managed_job_sample()
        return self._managed_job_sample

    def _load_active_cluster_sample(self) -> Dict[str, Any]:
        """Load an active cluster from database as a sample."""
        db_path = os.path.expanduser("~/.sky/state.db")

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM clusters WHERE name = ?",
                           (self.active_cluster_name,))
            row = cursor.fetchone()

            if not row:
                raise ValueError(
                    f"Active cluster '{self.active_cluster_name}' not found in database. "
                    f"Please create it first with: sky launch --infra k8s -c {self.active_cluster_name} -y \"echo 'test'\""
                )

            # Convert row to dict
            sample = dict(row)
            cursor.close()
            conn.close()

            return sample

        except Exception as e:
            raise ValueError(
                f"Failed to load active cluster sample: {e}") from e

    def _load_terminated_cluster_sample(self) -> Dict[str, Any]:
        """Load a terminated cluster from cluster_history as a sample."""
        db_path = os.path.expanduser("~/.sky/state.db")

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM cluster_history WHERE name = ?",
                           (self.terminated_cluster_name,))
            row = cursor.fetchone()

            if not row:
                raise ValueError(
                    f"Terminated cluster '{self.terminated_cluster_name}' not found in cluster_history. "
                    f"Please create and terminate it first with: "
                    f"sky launch --infra k8s -c {self.terminated_cluster_name} -y \"echo 'test'\" && "
                    f"sky down {self.terminated_cluster_name} -y")

            # Convert row to dict
            sample = dict(row)
            cursor.close()
            conn.close()

            return sample

        except Exception as e:
            raise ValueError(
                f"Failed to load terminated cluster sample: {e}") from e

    def _load_managed_job_sample(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Load a managed job from spot and job_info tables as a sample."""
        db_path = os.path.expanduser("~/.sky/spot_jobs.db")

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Load from spot table
            cursor.execute("SELECT * FROM spot WHERE job_id = ?",
                           (self.managed_job_id,))
            spot_row = cursor.fetchone()

            if not spot_row:
                raise ValueError(
                    f"Managed job with ID {self.managed_job_id} not found in spot table. "
                    f"Please create it first with: sky jobs launch --infra k8s \"sleep 10000000\""
                )

            spot_sample = dict(spot_row)

            # Load from job_info table
            cursor.execute("SELECT * FROM job_info WHERE spot_job_id = ?",
                           (spot_sample['spot_job_id'],))
            job_info_row = cursor.fetchone()

            if not job_info_row:
                raise ValueError(
                    f"Job info for spot_job_id {spot_sample['spot_job_id']} not found in job_info table."
                )

            job_info_sample = dict(job_info_row)

            cursor.close()
            conn.close()

            return (spot_sample, job_info_sample)

        except Exception as e:
            raise ValueError(f"Failed to load managed job sample: {e}") from e

    def generate_cluster_data(self, count: int) -> List[Dict[str, Any]]:
        """Generate test data for clusters table by cloning the sample cluster."""
        clusters = []
        current_time = time.time()

        for i in range(count):
            # Deep copy the sample cluster
            cluster = copy.deepcopy(self.active_cluster_sample)

            # Modify unique fields
            cluster['name'] = f"test-cluster-{i+1:04d}-{uuid.uuid4().hex[:8]}"
            cluster['cluster_hash'] = str(uuid.uuid4())
            cluster['launched_at'] = int(
                current_time - random.uniform(0, 7 * 24 * 3600))  # Last 7 days
            cluster['status_updated_at'] = int(
                current_time - random.uniform(0, 24 * 3600))  # Last day

            # Update handle with new cluster name if handle exists
            if cluster.get('handle'):
                cluster['handle'] = self._update_handle_cluster_name(
                    cluster['handle'], cluster['name'])

            clusters.append(cluster)

        return clusters

    def generate_cluster_history_data(self, recent_count: int,
                                      old_count: int) -> List[Dict[str, Any]]:
        """Generate cluster history data by cloning the terminated cluster sample."""
        history_clusters = []
        current_time = int(time.time())

        # Generate recent clusters (within 10 days)
        recent_min_days = 1 * 24 * 60 * 60  # 1 day ago
        recent_max_days = 9 * 24 * 60 * 60  # 9 days ago

        for i in range(recent_count):
            cluster = copy.deepcopy(self.terminated_cluster_sample)

            # Modify unique fields
            cluster[
                'name'] = f"test-cluster-recent-{i+1:04d}-{uuid.uuid4().hex[:8]}"
            cluster['cluster_hash'] = str(uuid.uuid4())

            # Random timestamp 1-9 days ago
            days_ago_seconds = random.randint(recent_min_days, recent_max_days)
            cluster['last_activity_time'] = current_time - days_ago_seconds
            cluster[
                'launched_at'] = cluster['last_activity_time'] - random.randint(
                    3600, 86400)

            history_clusters.append(cluster)

        # Generate older clusters (15-30 days ago)
        old_min_days = 15 * 24 * 60 * 60  # 15 days ago
        old_max_days = 30 * 24 * 60 * 60  # 30 days ago

        for i in range(old_count):
            cluster = copy.deepcopy(self.terminated_cluster_sample)

            # Modify unique fields
            cluster[
                'name'] = f"test-cluster-old-{i+1:04d}-{uuid.uuid4().hex[:8]}"
            cluster['cluster_hash'] = str(uuid.uuid4())

            # Random timestamp 15-30 days ago
            days_ago_seconds = random.randint(old_min_days, old_max_days)
            cluster['last_activity_time'] = current_time - days_ago_seconds
            cluster[
                'launched_at'] = cluster['last_activity_time'] - random.randint(
                    3600, 86400)

            history_clusters.append(cluster)

        return history_clusters

    def generate_managed_job_data(
            self,
            count: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Generate managed job data by cloning the sample job."""
        spot_jobs = []
        job_infos = []

        spot_sample, job_info_sample = self.managed_job_sample
        current_time = time.time()

        # Find the highest existing job_id to avoid conflicts
        db_path = os.path.expanduser("~/.sky/spot_jobs.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(job_id) FROM spot")
        max_job_id = cursor.fetchone()[0] or 0
        cursor.close()
        conn.close()

        starting_job_id = max_job_id + 1

        for i in range(count):
            # Deep copy the samples
            spot_job = copy.deepcopy(spot_sample)
            job_info = copy.deepcopy(job_info_sample)

            # Update unique IDs
            job_id = starting_job_id + i
            spot_job_id = starting_job_id + i

            spot_job['job_id'] = job_id
            spot_job['spot_job_id'] = spot_job_id
            job_info['spot_job_id'] = spot_job_id

            # Update timestamps
            base_time = current_time - random.uniform(0,
                                                      3600)  # Within last hour
            spot_job['submitted_at'] = base_time
            spot_job['start_at'] = base_time + random.uniform(30, 120)
            spot_job['last_recovered_at'] = spot_job['start_at']

            # Update run_timestamp to be unique
            timestamp_str = time.strftime('%Y-%m-%d-%H-%M-%S',
                                          time.localtime(base_time))
            spot_job[
                'run_timestamp'] = f'sky-{timestamp_str}-{random.randint(100000, 999999)}'

            # Update controller_pid to be unique
            job_info['controller_pid'] = -(random.randint(1000, 99999))

            # Update file paths to be unique
            home_dir = os.path.expanduser('~')
            job_hash = f'{i+1:04d}'
            job_info[
                'dag_yaml_path'] = f'{home_dir}/.sky/managed_jobs/test-job-{job_hash}.yaml'
            job_info[
                'env_file_path'] = f'{home_dir}/.sky/managed_jobs/test-job-{job_hash}.env'
            job_info[
                'original_user_yaml_path'] = f'{home_dir}/.sky/managed_jobs/test-job-{job_hash}.original_user_yaml'

            spot_jobs.append(spot_job)
            job_infos.append(job_info)

        return spot_jobs, job_infos

    def _update_handle_cluster_name(self, handle_blob: bytes,
                                    new_cluster_name: str) -> bytes:
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
