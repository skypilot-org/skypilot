#!/usr/bin/env python3
"""
Script to inject production-scale test data into SkyPilot databases.

This script injects realistic production-scale data based on:
- 1.5K running clusters
- 220K history clusters
- 290K cluster events
- 12.5K managed jobs

Usage:
    python tests/load_tests/db_scale_tests/inject_production_scale_data.py \
        --active-cluster scale-test-active \
        --terminated-cluster scale-test-terminated \
        --managed-job-id 1
"""

import argparse
import copy
import os
import random
import sys
import time
import uuid

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from sample_based_generator import SampleBasedGenerator
from scale_test_utils import TestScale


class ProductionScaleGenerator(SampleBasedGenerator):
    """Extended generator for production-scale data with multiple users."""

    def __init__(self,
                 active_cluster_name: str,
                 terminated_cluster_name: str,
                 managed_job_id: int,
                 num_users: int = 10,
                 db_connection_getter=None,
                 format_sql=None):
        """Initialize the generator.

        Args:
            active_cluster_name: Name of a running cluster to use as template
            terminated_cluster_name: Name of a terminated cluster to use as template
            managed_job_id: Job ID of a managed job to use as template
            num_users: Number of users to simulate
            db_connection_getter: Optional function to get database connection
            format_sql: Optional function to format SQL queries
        """
        super().__init__(active_cluster_name,
                         terminated_cluster_name,
                         managed_job_id,
                         db_connection_getter=db_connection_getter,
                         format_sql=format_sql)
        self.num_users = num_users
        self.user_hashes = [
            f"user-{i:02d}-{uuid.uuid4().hex[:8]}" for i in range(num_users)
        ]

    def _get_random_user_hash(self):
        """Get a random user hash."""
        return random.choice(self.user_hashes)

    def generate_production_cluster_data(self, count: int = 1500):
        """Generate production-scale cluster data with multiple users."""
        clusters = []
        current_time = time.time()

        for i in range(count):
            # Deep copy the sample cluster
            cluster = copy.deepcopy(self.active_cluster_sample)

            # Modify unique fields
            cluster['name'] = f"prod-cluster-{i+1:05d}-{uuid.uuid4().hex[:8]}"
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

    def generate_production_cluster_history_data(self, count: int = 220000):
        """Generate production-scale cluster history data.

        Distributes across different time ranges:
        - Recent (last 30 days): 20%
        - Medium (30-90 days): 30%
        - Old (90-365 days): 50%
        """
        history_clusters = []
        current_time = int(time.time())

        # Calculate counts for each time range
        recent_count = int(count * 0.2)
        medium_count = int(count * 0.3)
        old_count = count - recent_count - medium_count

        # Generate recent clusters (1-30 days ago)
        for i in range(recent_count):
            cluster = copy.deepcopy(self.terminated_cluster_sample)

            cluster[
                'name'] = f"prod-hist-recent-{i+1:06d}-{uuid.uuid4().hex[:8]}"
            cluster['cluster_hash'] = str(uuid.uuid4())

            days_ago_seconds = random.randint(1 * 24 * 60 * 60,
                                              30 * 24 * 60 * 60)
            cluster['last_activity_time'] = current_time - days_ago_seconds
            cluster[
                'launched_at'] = cluster['last_activity_time'] - random.randint(
                    3600, 86400 * 7)

            history_clusters.append(cluster)

        # Generate medium clusters (30-90 days ago)
        for i in range(medium_count):
            cluster = copy.deepcopy(self.terminated_cluster_sample)

            cluster[
                'name'] = f"prod-hist-medium-{i+1:06d}-{uuid.uuid4().hex[:8]}"
            cluster['cluster_hash'] = str(uuid.uuid4())

            days_ago_seconds = random.randint(30 * 24 * 60 * 60,
                                              90 * 24 * 60 * 60)
            cluster['last_activity_time'] = current_time - days_ago_seconds
            cluster[
                'launched_at'] = cluster['last_activity_time'] - random.randint(
                    3600, 86400 * 7)

            history_clusters.append(cluster)

        # Generate old clusters (90-365 days ago)
        for i in range(old_count):
            cluster = copy.deepcopy(self.terminated_cluster_sample)

            cluster['name'] = f"prod-hist-old-{i+1:06d}-{uuid.uuid4().hex[:8]}"
            cluster['cluster_hash'] = str(uuid.uuid4())

            days_ago_seconds = random.randint(90 * 24 * 60 * 60,
                                              365 * 24 * 60 * 60)
            cluster['last_activity_time'] = current_time - days_ago_seconds
            cluster[
                'launched_at'] = cluster['last_activity_time'] - random.randint(
                    3600, 86400 * 7)

            history_clusters.append(cluster)

        return history_clusters


class ProductionScaleTest(TestScale):
    """Extended TestScale for production-scale data injection."""

    def initialize(self,
                   active_cluster_name,
                   terminated_cluster_name,
                   managed_job_id,
                   num_users=10,
                   sql_url=None):
        """Initialize with production-scale generator.

        Args:
            active_cluster_name: Name of active cluster template
            terminated_cluster_name: Name of terminated cluster template
            managed_job_id: Job ID template
            num_users: Number of users to simulate
            sql_url: Optional PostgreSQL connection URL (e.g., postgresql://user:pass@host:port/db)
                    If None, uses SQLite databases
        """
        # Initialize parent class with PostgreSQL support
        super().initialize(active_cluster_name,
                           terminated_cluster_name,
                           managed_job_id,
                           sql_url=sql_url)

        # Replace generator with production-scale generator
        self.generator = ProductionScaleGenerator(
            active_cluster_name=active_cluster_name,
            terminated_cluster_name=terminated_cluster_name,
            managed_job_id=managed_job_id,
            num_users=num_users,
            db_connection_getter=self._get_connection
            if self.use_postgres else None,
            format_sql=self._format_sql if self.use_postgres else None)

    def inject_production_clusters(self, count: int = 1500):
        """Inject production-scale clusters."""
        print(f"Injecting {count} production-scale clusters...")

        # Generate test data
        clusters = self.generator.generate_production_cluster_data(count)

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Load cluster YAML for the sample cluster
            cursor.execute(
                self._format_sql(
                    "SELECT yaml FROM cluster_yaml WHERE cluster_name = ?"),
                (self.generator.active_cluster_name,))
            sample_yaml_row = cursor.fetchone()
            sample_yaml = sample_yaml_row[0] if sample_yaml_row else None

            # Get column names from first cluster dict
            cluster_columns = list(clusters[0].keys())
            columns_str = ', '.join(cluster_columns)
            placeholders = ', '.join(['?' for _ in cluster_columns])

            insert_sql = self._format_sql(
                f"INSERT INTO clusters ({columns_str}) VALUES ({placeholders})")

            # ON CONFLICT syntax (both databases support it, but PostgreSQL uses EXCLUDED vs excluded)
            yaml_insert_sql = self._format_sql(f"""
                INSERT INTO cluster_yaml (cluster_name, yaml)
                VALUES (?, ?)
                ON CONFLICT(cluster_name) DO UPDATE SET yaml = {self._get_excluded_keyword()}.yaml
            """)

            # Insert in batches for performance
            batch_size = 100
            total_inserted = 0

            for i in range(0, len(clusters), batch_size):
                batch = clusters[i:i + batch_size]
                batch_data = []

                for cluster in batch:
                    row_data = tuple(cluster[col] for col in cluster_columns)
                    batch_data.append(row_data)

                self._execute_batch(cursor,
                                    insert_sql,
                                    batch_data,
                                    page_size=batch_size)
                conn.commit()

                if sample_yaml:
                    yaml_batch_data = [
                        (cluster['name'], sample_yaml) for cluster in batch
                    ]
                    self._execute_batch(cursor,
                                        yaml_insert_sql,
                                        yaml_batch_data,
                                        page_size=batch_size)
                    conn.commit()

                total_inserted += len(batch_data)

                if (i + batch_size) % 1000 == 0:
                    print(
                        f"  Progress: {total_inserted}/{count} clusters inserted..."
                    )

            # Store names for cleanup
            self.test_cluster_names.extend(
                [cluster['name'] for cluster in clusters])

            print(f"Injected {total_inserted} production clusters")
            cursor.close()
            conn.close()
            return total_inserted

        except Exception as e:
            print(f"Failed to inject production clusters: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def inject_production_cluster_history(self, count: int = 220000):
        """Inject production-scale cluster history."""
        print(f"Injecting {count} production-scale cluster history entries...")

        # Generate test data
        history_clusters = self.generator.generate_production_cluster_history_data(
            count)

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get column names from first cluster dict
            cluster_columns = list(history_clusters[0].keys())
            columns_str = ', '.join(cluster_columns)
            placeholders = ', '.join(['?' for _ in cluster_columns])

            insert_sql = self._format_sql(
                f"INSERT INTO cluster_history ({columns_str}) VALUES ({placeholders})"
            )

            batch_size = 100
            total_inserted = 0

            for i in range(0, len(history_clusters), batch_size):
                batch = history_clusters[i:i + batch_size]
                batch_data = []

                for cluster in batch:
                    row_data = tuple(cluster[col] for col in cluster_columns)
                    batch_data.append(row_data)
                    self.test_cluster_hashes.append(cluster['cluster_hash'])

                self._execute_batch(cursor,
                                    insert_sql,
                                    batch_data,
                                    page_size=batch_size)
                conn.commit()
                total_inserted += len(batch_data)

                if (i + batch_size) % 10000 == 0:
                    print(
                        f"  Progress: {total_inserted}/{count} entries inserted..."
                    )

            cursor.close()
            conn.close()
            print(f"Injected {total_inserted} cluster history entries")
            return total_inserted

        except Exception as e:
            print(f"Failed to inject cluster history: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def inject_cluster_events(self, count: int = 290000):
        """Inject cluster events for both active and history clusters."""
        print(f"Injecting {count} cluster events...")

        # Event types and reasons
        event_reasons = [
            'Cluster launched',
            'Cluster started',
            'Cluster stopped',
            'Cluster terminated',
            'Status update',
            'Autostop triggered',
            'Autodown triggered',
            'Recovery started',
            'Recovery completed',
            'Provisioning started',
            'Provisioning completed',
        ]

        statuses = ['INIT', 'UP', 'STOPPED']

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get all cluster hashes (both active and history)
            cursor.execute("SELECT cluster_hash, name FROM clusters")
            active_clusters = cursor.fetchall()

            cursor.execute(
                self._format_sql(
                    "SELECT cluster_hash, name FROM cluster_history LIMIT ?"),
                (min(count // 2,
                     100000),))  # Limit history clusters for performance
            history_clusters = cursor.fetchall()

            all_clusters = active_clusters + history_clusters
            if not all_clusters:
                print(
                    "Warning: No clusters found. Please inject clusters first.")
                cursor.close()
                conn.close()
                return 0

            print(f"  Generating events across {len(all_clusters)} clusters...")

            # Generate events
            events = []
            current_time = int(time.time())

            # Distribute events evenly across clusters
            events_per_cluster = max(1, count // len(all_clusters))
            remaining_events = count

            for cluster_hash, cluster_name in all_clusters:
                if remaining_events <= 0:
                    break

                # Generate 1-5 events per cluster
                num_events = min(events_per_cluster, remaining_events,
                                 random.randint(1, 5))
                if num_events <= 0:
                    continue

                # Generate events with timestamps spread over time (last 30 days)
                base_time = current_time - random.randint(0, 30 * 24 * 60 * 60)

                for j in range(num_events):
                    starting_status = random.choice(statuses)
                    ending_status = random.choice(statuses)
                    reason = random.choice(event_reasons)
                    # Events 1 min to 1 hour apart
                    transitioned_at = base_time + j * random.randint(60, 3600)

                    events.append({
                        'cluster_hash': cluster_hash,
                        'name': cluster_name,
                        'starting_status': starting_status,
                        'ending_status': ending_status,
                        'reason': reason,
                        'transitioned_at': transitioned_at,
                        'type': 'STATUS_CHANGE',
                        'request_id': f"req-{uuid.uuid4().hex[:16]}"
                    })

                    remaining_events -= 1
                    if remaining_events <= 0:
                        break

            # Fill any remaining events randomly
            while remaining_events > 0 and all_clusters:
                cluster_hash, cluster_name = random.choice(all_clusters)
                starting_status = random.choice(statuses)
                ending_status = random.choice(statuses)
                reason = random.choice(event_reasons)
                transitioned_at = current_time - random.randint(
                    0, 30 * 24 * 60 * 60)

                events.append({
                    'cluster_hash': cluster_hash,
                    'name': cluster_name,
                    'starting_status': starting_status,
                    'ending_status': ending_status,
                    'reason': reason,
                    'transitioned_at': transitioned_at,
                    'type': 'STATUS_CHANGE',
                    'request_id': f"req-{uuid.uuid4().hex[:16]}"
                })
                remaining_events -= 1

            print(
                f"  Generated {len(events)} events, inserting into database...")

            # Insert events in batches
            insert_sql = self._format_sql("""
                INSERT INTO cluster_events
                (cluster_hash, name, starting_status, ending_status, reason,
                 transitioned_at, type, request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """)

            batch_size = 100
            total_inserted = 0

            for i in range(0, len(events), batch_size):
                batch = events[i:i + batch_size]
                batch_data = [
                    (e['cluster_hash'], e['name'], e['starting_status'],
                     e['ending_status'], e['reason'], e['transitioned_at'],
                     e['type'], e['request_id']) for e in batch
                ]

                try:
                    self._execute_batch(cursor,
                                        insert_sql,
                                        batch_data,
                                        page_size=batch_size)
                    conn.commit()
                    total_inserted += len(batch_data)
                except self._get_integrity_error_class():
                    # Skip duplicate events (same cluster_hash, reason, transitioned_at)
                    conn.rollback()
                    # Try inserting one by one
                    for event_data in batch_data:
                        try:
                            cursor.execute(insert_sql, event_data)
                            conn.commit()
                            total_inserted += 1
                        except self._get_integrity_error_class():
                            conn.rollback()
                            continue

                if (i + batch_size) % 10000 == 0:
                    print(
                        f"  Progress: {total_inserted}/{len(events)} events inserted..."
                    )

            cursor.close()
            conn.close()
            print(f"Injected {total_inserted} cluster events")
            return total_inserted

        except Exception as e:
            print(f"Failed to inject cluster events: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def cleanup_production_data(self, managed_job_id: int):
        """Clean up all production-scale data that was injected."""
        print("Cleaning up production-scale data...")
        print("=" * 80)

        results = {}

        try:
            # Clean up active clusters
            conn = self._get_connection()
            cursor = conn.cursor()

            # Clean up production clusters (inject_production_clusters creates prod-cluster-*)
            # Delete from cluster_yaml first
            cursor.execute(
                "DELETE FROM cluster_yaml WHERE cluster_name LIKE 'prod-cluster-%'"
            )
            yaml_deleted = cursor.rowcount

            # Delete from clusters
            cursor.execute(
                "DELETE FROM clusters WHERE name LIKE 'prod-cluster-%'")
            clusters_deleted = cursor.rowcount
            conn.commit()

            if clusters_deleted > 0:
                print(
                    f"\n[1/4] Cleaning up {clusters_deleted} active clusters..."
                )
                results['clusters'] = clusters_deleted
                results['yaml'] = yaml_deleted
                print(
                    f"  Deleted {clusters_deleted} clusters and {yaml_deleted} yaml entries"
                )
            else:
                print("\n[1/4] No production clusters found to clean up")
                results['clusters'] = 0

            # Clean up cluster history (inject_production_cluster_history creates prod-hist-*)
            cursor.execute(
                "DELETE FROM cluster_history WHERE name LIKE 'prod-hist-%'")
            history_deleted = cursor.rowcount
            conn.commit()

            if history_deleted > 0:
                print(
                    f"\n[2/4] Cleaning up {history_deleted} cluster history entries..."
                )
                results['history'] = history_deleted
                print(f"  Deleted {history_deleted} cluster history entries")
            else:
                print("\n[2/4] No production cluster history found to clean up")
                results['history'] = 0

            # Clean up cluster events for production clusters
            cursor.execute("""
                DELETE FROM cluster_events
                WHERE name LIKE 'prod-cluster-%' OR name LIKE 'prod-hist-%'
            """)
            events_deleted = cursor.rowcount
            conn.commit()

            if events_deleted > 0:
                print(f"\n[3/4] Cleaning up cluster events...")
                results['events'] = events_deleted
                print(f"  Deleted {events_deleted} cluster events")
            else:
                print("\n[3/4] No production cluster events found to clean up")
                results['events'] = 0

            cursor.close()
            conn.close()

            # Clean up managed jobs
            conn = self._get_connection(for_jobs=True)
            cursor = conn.cursor()

            # Delete from job_info first (to handle foreign key constraints if any)
            cursor.execute(
                self._format_sql("""
                DELETE FROM job_info
                WHERE spot_job_id > ?
            """), (managed_job_id,))
            job_info_deleted = cursor.rowcount

            # Delete from spot
            cursor.execute(
                self._format_sql("""
                DELETE FROM spot
                WHERE job_id > ?
            """), (managed_job_id,))
            jobs_deleted = cursor.rowcount
            conn.commit()

            if jobs_deleted > 0:
                print(f"\n[4/4] Cleaning up {jobs_deleted} managed jobs...")
                results['jobs'] = jobs_deleted
                print(f"  Deleted {jobs_deleted} managed jobs")
            else:
                print(
                    f"\n[4/4] No managed jobs found with job_id > {managed_job_id}"
                )
                results['jobs'] = 0

            cursor.close()
            conn.close()

            # Print summary
            print("\n" + "=" * 80)
            print("CLEANUP SUMMARY")
            print("=" * 80)
            print(f"Active clusters deleted: {results.get('clusters', 0):,}")
            if 'yaml' in results:
                print(f"Cluster YAML entries deleted: {results['yaml']:,}")
            print(f"History clusters deleted: {results.get('history', 0):,}")
            print(f"Cluster events deleted: {results.get('events', 0):,}")
            print(f"Managed jobs deleted: {results.get('jobs', 0):,}")
            print("=" * 80)
            print("\nCleanup completed successfully!")

            return results

        except Exception as e:
            print(f"\nCleanup failed: {e}")
            import traceback
            traceback.print_exc()
            return None


def main():
    """Main entry point for production-scale data injection."""
    parser = argparse.ArgumentParser(
        description='Inject production-scale test data into SkyPilot databases')
    parser.add_argument(
        '--active-cluster',
        type=str,
        default='scale-test-active',
        help=
        'Name of the active cluster to use as template (default: scale-test-active)'
    )
    parser.add_argument(
        '--terminated-cluster',
        type=str,
        default='scale-test-terminated',
        help=
        'Name of the terminated cluster to use as template (default: scale-test-terminated)'
    )
    parser.add_argument(
        '--managed-job-id',
        type=int,
        default=1,
        help='Job ID of the managed job to use as template (default: 1)')
    parser.add_argument('--num-users',
                        type=int,
                        default=10,
                        help='Number of users to simulate (default: 10)')
    parser.add_argument(
        '--active-clusters',
        type=int,
        default=1500,
        help='Number of active clusters to inject (default: 1500)')
    parser.add_argument(
        '--history-clusters',
        type=int,
        default=220000,
        help='Number of history clusters to inject (default: 220000)')
    parser.add_argument(
        '--cluster-events',
        type=int,
        default=290000,
        help='Number of cluster events to inject (default: 290000)')
    parser.add_argument(
        '--managed-jobs',
        type=int,
        default=12500,
        help='Number of managed jobs to inject (default: 12500)')
    parser.add_argument('--skip-clusters',
                        action='store_true',
                        help='Skip injecting active clusters')
    parser.add_argument('--skip-history',
                        action='store_true',
                        help='Skip injecting cluster history')
    parser.add_argument('--skip-events',
                        action='store_true',
                        help='Skip injecting cluster events')
    parser.add_argument('--skip-jobs',
                        action='store_true',
                        help='Skip injecting managed jobs')
    parser.add_argument(
        '--cleanup',
        action='store_true',
        help='Clean up (undo) all production-scale data instead of injecting')
    parser.add_argument(
        '--sql-url',
        type=str,
        default=None,
        help=
        'PostgreSQL connection URL (e.g., postgresql://user:pass@host:port/db). '
        'If not provided, uses SQLite databases.')

    args = parser.parse_args()

    # Handle cleanup mode
    if args.cleanup:
        print("=" * 80)
        print("PRODUCTION-SCALE DATA CLEANUP")
        print("=" * 80)
        print(f"Template managed job ID: {args.managed_job_id}")
        print("=" * 80)

        # Create test instance (minimal init for cleanup)
        test = ProductionScaleTest()
        if args.sql_url:
            test.initialize(active_cluster_name='dummy',
                            terminated_cluster_name='dummy',
                            managed_job_id=args.managed_job_id,
                            sql_url=args.sql_url)
        else:
            test.db_path = os.path.expanduser("~/.sky/state.db")
            test.jobs_db_path = os.path.expanduser("~/.sky/spot_jobs.db")
            test.use_postgres = False

        try:
            results = test.cleanup_production_data(args.managed_job_id)
            if results is None:
                sys.exit(1)
        except Exception as e:
            print(f"\nCleanup failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    print("=" * 80)
    print("PRODUCTION-SCALE DATA INJECTION")
    print("=" * 80)
    print(f"Active clusters: {args.active_clusters}")
    print(f"History clusters: {args.history_clusters}")
    print(f"Cluster events: {args.cluster_events}")
    print(f"Managed jobs: {args.managed_jobs}")
    print(f"Number of users: {args.num_users}")
    print("=" * 80)

    # Create test instance
    test = ProductionScaleTest()
    test.initialize(active_cluster_name=args.active_cluster,
                    terminated_cluster_name=args.terminated_cluster,
                    managed_job_id=args.managed_job_id,
                    num_users=args.num_users,
                    sql_url=args.sql_url)

    results = {}
    start_time = time.time()

    try:
        # Inject active clusters
        if not args.skip_clusters:
            print("\n[1/4] Injecting active clusters...")
            results['clusters'] = test.inject_production_clusters(
                args.active_clusters)
        else:
            print("\n[1/4] Skipping active clusters...")

        # Inject cluster history
        if not args.skip_history:
            print("\n[2/4] Injecting cluster history...")
            results['history'] = test.inject_production_cluster_history(
                args.history_clusters)
        else:
            print("\n[2/4] Skipping cluster history...")

        # Inject cluster events
        if not args.skip_events:
            print("\n[3/4] Injecting cluster events...")
            results['events'] = test.inject_cluster_events(args.cluster_events)
        else:
            print("\n[3/4] Skipping cluster events...")

        # Inject managed jobs
        if not args.skip_jobs:
            print("\n[4/4] Injecting managed jobs...")
            results['jobs'] = test.inject_managed_jobs(args.managed_jobs)
        else:
            print("\n[4/4] Skipping managed jobs...")

        # Print summary
        total_time = time.time() - start_time
        print("\n" + "=" * 80)
        print("INJECTION SUMMARY")
        print("=" * 80)
        if 'clusters' in results:
            print(f"Active clusters: {results['clusters']:,}")
        if 'history' in results:
            print(f"History clusters: {results['history']:,}")
        if 'events' in results:
            print(f"Cluster events: {results['events']:,}")
        if 'jobs' in results:
            print(f"Managed jobs: {results['jobs']:,}")
        print(f"\nTotal time: {total_time:.1f}s")
        print("=" * 80)
        print("\nData injection completed successfully!")
        print("\nNote: This data will persist in your databases.")
        print(
            f"To clean up, run: python {sys.argv[0]} --cleanup --managed-job-id {args.managed_job_id}"
        )

    except Exception as e:
        print(f"\nData injection failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
