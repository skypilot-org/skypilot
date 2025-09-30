#!/usr/bin/env python3
"""
Simple script to run comprehensive scale tests manually.
"""

import os
import sys
import time

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from test_scale import TestScale

from sky import global_user_state


def main():
    """Run the comprehensive scale tests manually."""
    print("Starting Comprehensive Scale Test")
    print("=" * 60)

    # Create test instance
    test = TestScale()
    test.setup_method()

    try:
        print("\n1. Testing Active Clusters (2000 clusters)")
        print("-" * 40)

        # Test 1: Active clusters
        active_count = test.inject_clusters(2000)
        print(f"   Injected: {active_count} active clusters")

        # Benchmark get_clusters
        start_time = time.time()
        clusters = global_user_state.get_clusters(
            exclude_managed_clusters=False, summary_response=False)
        get_clusters_duration = time.time() - start_time
        print(
            f"   get_clusters: {len(clusters)} clusters in {get_clusters_duration:.3f}s"
        )
        print(
            f"   Rate: {len(clusters)/get_clusters_duration:.0f} clusters/sec")

        # Benchmark sky status
        print("\n   Testing sky status performance:")
        import subprocess
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
            print(f"   sky status failed: {result.stderr}")

        print(
            "\n2. Testing Cluster History (2000 recent + 8000 older terminated clusters)"
        )
        print("-" * 60)

        # Test 2: Add cluster history (2000 recent + 8000 old)
        history_count = test.inject_cluster_history(recent_count=2000,
                                                    old_count=8000)
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

        print("\n" + "=" * 60)
        print("SCALE TEST SUMMARY")
        print("=" * 60)
        print(f"Active Clusters: {active_count}")
        print(f"Terminated Clusters: {history_count}")
        print(f"Total Dataset: {active_count + history_count} clusters")
        print(
            f"get_clusters (active only): {len(clusters)} in {get_clusters_duration:.3f}s"
        )
        print(
            f"get_clusters_from_history (10 days): {len(history_10)} in {duration_10:.3f}s"
        )
        print(
            f"get_clusters_from_history (30 days): {len(history_30)} in {duration_30:.3f}s"
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
