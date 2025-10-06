#!/usr/bin/env python3
"""
Simple script to inject test clusters without cleanup for manual verification.
"""

import argparse
import os
import sys

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from scale_test_utils import TestScale


def main():
    """Inject test clusters without cleanup."""
    parser = argparse.ArgumentParser(
        description='Inject test clusters for manual verification')
    parser.add_argument(
        '--active-cluster',
        type=str,
        default='scale-test-active',
        help='Name of the active cluster to use as template (default: scale-test-active)'
    )
    parser.add_argument(
        '--count',
        type=int,
        default=5,
        help='Number of test clusters to inject (default: 5)')

    args = parser.parse_args()

    print(f"Injecting {args.count} test clusters (no cleanup)...")
    print("=" * 60)

    # Create test instance
    test = TestScale()
    test.initialize(
        active_cluster_name=args.active_cluster,
        terminated_cluster_name='scale-test-terminated',
        managed_job_id=1  # Not used
    )

    try:
        # Inject clusters
        injected = test.inject_clusters(args.count)
        print(f"\nSuccessfully injected {injected} test clusters!")
        print("\nCluster names:")
        for name in test.test_cluster_names:
            print(f"  - {name}")

        print("\n" + "=" * 60)
        print("Test clusters injected. You can now run:")
        print("  sky status")
        print("\nTo clean up these clusters later, run:")
        print("  python tests/scale_tests/cleanup_test_clusters.py")

    except Exception as e:
        print(f"\nFailed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
