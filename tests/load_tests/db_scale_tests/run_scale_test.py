#!/usr/bin/env python3
"""
Simple script to run comprehensive scale tests manually.
"""

import argparse
import os
import subprocess
import sys
import time

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from scale_test_utils import TestScale

from sky import global_user_state
from sky.jobs import state as job_state


def test_active_clusters(test, count=2000):
    """Test active clusters performance."""
    print(f"\n1. Testing Active Clusters ({count} clusters)")
    print("-" * 60)

    # Inject test data
    active_count = test.inject_clusters(count)
    print(f"   Injected: {active_count} active clusters")

    # Benchmark get_clusters
    start_time = time.time()
    clusters = global_user_state.get_clusters(exclude_managed_clusters=False,
                                              summary_response=False)
    get_clusters_duration = time.time() - start_time
    print(
        f"   get_clusters: {len(clusters)} clusters in {get_clusters_duration:.3f}s"
    )
    print(f"   Rate: {len(clusters)/get_clusters_duration:.0f} clusters/sec")

    # Benchmark sky status
    print("\n   Testing sky status performance:")
    start_time = time.time()
    result = subprocess.run(['sky', 'status'],
                            capture_output=True,
                            text=True,
                            timeout=30)
    sky_status_duration = time.time() - start_time

    if result.returncode == 0:
        output_lines = result.stdout.split('\n')
        cluster_lines = [
            line for line in output_lines if 'test-cluster-' in line
        ]
        print(
            f"   sky status: {len(cluster_lines)} test clusters shown in {sky_status_duration:.3f}s"
        )
    else:
        if "Cluster yaml" in result.stderr and "not found" in result.stderr:
            print(
                f"   sky status: Skipped due to missing YAML files (expected) - {sky_status_duration:.3f}s"
            )
        else:
            print(f"   sky status failed: {result.stderr}")

    return {
        'injected': active_count,
        'retrieved': len(clusters),
        'duration': get_clusters_duration
    }


def test_cluster_history(test, recent_count=2000, old_count=8000):
    """Test cluster history performance."""
    print(
        f"\n2. Testing Cluster History ({recent_count} recent + {old_count} older)"
    )
    print("-" * 60)

    # Inject cluster history
    history_count = test.inject_cluster_history(recent_count=recent_count,
                                                old_count=old_count)
    print(f"   Total injected: {history_count} cluster history entries")

    # Test get_clusters_from_history with days=10 (recent clusters only)
    print("\n   Testing get_clusters_from_history(days=10):")
    start_time = time.time()
    history_10 = global_user_state.get_clusters_from_history(days=10)
    duration_10 = time.time() - start_time
    print(
        f"   get_clusters_from_history(days=10): {len(history_10)} clusters in {duration_10:.3f}s"
    )
    print(f"   Rate: {len(history_10)/duration_10:.0f} clusters/sec")

    # Test get_clusters_from_history with days=30 (all terminated clusters)
    print("\n   Testing get_clusters_from_history(days=30):")
    start_time = time.time()
    history_30 = global_user_state.get_clusters_from_history(days=30)
    duration_30 = time.time() - start_time
    print(
        f"   get_clusters_from_history(days=30): {len(history_30)} clusters in {duration_30:.3f}s"
    )
    print(f"   Rate: {len(history_30)/duration_30:.0f} clusters/sec")

    return {
        'injected': history_count,
        'recent_retrieved': len(history_10),
        'all_retrieved': len(history_30),
        'duration_10': duration_10,
        'duration_30': duration_30
    }


def test_managed_jobs(test, count=10000):
    """Test managed jobs performance."""
    print(f"\n3. Testing Managed Jobs ({count} jobs)")
    print("-" * 60)

    # Inject managed jobs
    jobs_count = test.inject_managed_jobs(count)
    print(f"   Total injected: {jobs_count} managed jobs")

    # Test get_managed_jobs (raw DB query)
    print("\n   Testing get_managed_jobs (raw DB query):")
    start_time = time.time()
    managed_jobs = job_state.get_managed_jobs()
    duration_get_jobs = time.time() - start_time
    print(
        f"   get_managed_jobs: {len(managed_jobs)} jobs in {duration_get_jobs:.3f}s"
    )
    print(f"   Rate: {len(managed_jobs)/duration_get_jobs:.0f} jobs/sec")

    # Test get_managed_job_queue (filtering + enrichment on controller)
    print("\n   Testing get_managed_job_queue (filtering + enrichment):")
    from sky.jobs import utils as managed_job_utils
    from sky.workspaces import core as workspaces_core

    accessible_workspaces = list(workspaces_core.get_workspaces().keys())
    start_time = time.time()
    queue_result = managed_job_utils.get_managed_job_queue(
        skip_finished=False, accessible_workspaces=accessible_workspaces)
    duration_get_queue = time.time() - start_time
    print(
        f"   get_managed_job_queue: {queue_result['total']} jobs in {duration_get_queue:.3f}s"
    )
    print(f"   Rate: {queue_result['total']/duration_get_queue:.0f} jobs/sec")

    # Test load_managed_job_queue (JSON deserialization from SSH payload)
    print("\n   Testing load_managed_job_queue (JSON deserialization):")
    from sky.utils import message_utils

    # Simulate the payload that would come from SSH run_on_head
    payload_str = message_utils.encode_payload(queue_result)
    payload_size_mb = len(payload_str) / (1024 * 1024)

    start_time = time.time()
    loaded_jobs, total, result_type, total_no_filter, status_counts = \
        managed_job_utils.load_managed_job_queue(payload_str)
    duration_load = time.time() - start_time
    print(
        f"   load_managed_job_queue: {len(loaded_jobs)} jobs in {duration_load:.3f}s"
    )
    print(f"   Rate: {len(loaded_jobs)/duration_load:.0f} jobs/sec")
    print(f"   Payload size: {payload_size_mb:.2f} MB")

    # Test sky jobs queue (full CLI path)
    print("\n   Testing sky jobs queue (full CLI path):")
    start_time = time.time()
    result = subprocess.run(['sky', 'jobs', 'queue'],
                            capture_output=True,
                            text=True,
                            timeout=30)
    sky_jobs_queue_duration = time.time() - start_time

    if result.returncode == 0:
        output_lines = result.stdout.split('\n')
        job_lines = [
            line for line in output_lines
            if 'sky-cmd' in line and 'RUNNING' in line
        ]
        print(
            f"   sky jobs queue: {len(job_lines)} test jobs shown in {sky_jobs_queue_duration:.3f}s"
        )
    else:
        print(f"   sky jobs queue failed: {result.stderr}")

    return {
        'injected': jobs_count,
        'retrieved': len(managed_jobs),
        'duration_get_jobs': duration_get_jobs,
        'duration_get_queue': duration_get_queue,
        'duration_load': duration_load,
        'payload_size_mb': payload_size_mb,
        'duration_cli': sky_jobs_queue_duration
    }


def main():
    """Run the comprehensive scale tests manually."""
    parser = argparse.ArgumentParser(
        description='Run SkyPilot scale tests with sample data')
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
    parser.add_argument('--test',
                        type=str,
                        choices=['all', 'clusters', 'history', 'jobs'],
                        default='all',
                        help='Which test to run (default: all)')
    parser.add_argument(
        '--cluster-count',
        type=int,
        default=2000,
        help='Number of active clusters to inject (default: 2000)')
    parser.add_argument(
        '--history-recent',
        type=int,
        default=2000,
        help='Number of recent terminated clusters to inject (default: 2000)')
    parser.add_argument(
        '--history-old',
        type=int,
        default=8000,
        help='Number of old terminated clusters to inject (default: 8000)')
    parser.add_argument(
        '--job-count',
        type=int,
        default=10000,
        help='Number of managed jobs to inject (default: 10000)')

    args = parser.parse_args()

    print("Starting Scale Test")
    print("=" * 60)
    print(f"Test mode: {args.test}")
    print(f"Using sample data:")
    print(f"  Active cluster: {args.active_cluster}")
    print(f"  Terminated cluster: {args.terminated_cluster}")
    print(f"  Managed job ID: {args.managed_job_id}")
    print("=" * 60)

    # Create test instance
    test = TestScale()
    test.initialize(active_cluster_name=args.active_cluster,
                    terminated_cluster_name=args.terminated_cluster,
                    managed_job_id=args.managed_job_id)

    results = {}

    try:
        # Run selected tests
        if args.test in ['all', 'clusters']:
            results['clusters'] = test_active_clusters(test, args.cluster_count)

        if args.test in ['all', 'history']:
            results['history'] = test_cluster_history(test, args.history_recent,
                                                      args.history_old)

        if args.test in ['all', 'jobs']:
            results['jobs'] = test_managed_jobs(test, args.job_count)

        # Print summary
        print("\n" + "=" * 60)
        print("SCALE TEST SUMMARY")
        print("=" * 60)

        if 'clusters' in results:
            print(f"Active Clusters:")
            print(f"  Injected: {results['clusters']['injected']}")
            print(f"  Retrieved: {results['clusters']['retrieved']}")
            print(f"  Duration: {results['clusters']['duration']:.3f}s")

        if 'history' in results:
            print(f"\nCluster History:")
            print(f"  Injected: {results['history']['injected']}")
            print(
                f"  Recent (10 days): {results['history']['recent_retrieved']}")
            print(f"  All (30 days): {results['history']['all_retrieved']}")
            print(
                f"  Duration (10 days): {results['history']['duration_10']:.3f}s"
            )
            print(
                f"  Duration (30 days): {results['history']['duration_30']:.3f}s"
            )

        if 'jobs' in results:
            print(f"\nManaged Jobs:")
            print(f"  Injected: {results['jobs']['injected']}")
            print(f"  Retrieved: {results['jobs']['retrieved']}")
            print(
                f"  get_managed_jobs: {results['jobs']['duration_get_jobs']:.3f}s"
            )
            print(
                f"  get_managed_job_queue: {results['jobs']['duration_get_queue']:.3f}s"
            )
            print(
                f"  load_managed_job_queue: {results['jobs']['duration_load']:.3f}s"
            )
            print(
                f"  Payload size: {results['jobs']['payload_size_mb']:.2f} MB")
            print(
                f"  CLI (sky jobs queue): {results['jobs']['duration_cli']:.3f}s"
            )

        print("\nTest completed successfully!")

    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Always cleanup
        print("\nCleaning up...")
        test.teardown_method()


if __name__ == "__main__":
    main()
