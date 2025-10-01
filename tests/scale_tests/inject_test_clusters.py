#!/usr/bin/env python3
"""
Simple script to inject test clusters without cleanup for manual verification.
"""

import os
import sys

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from test_scale import TestScale


def main():
    """Inject test clusters without cleanup."""
    count = 5

    print(f"Injecting {count} test clusters (no cleanup)...")
    print("=" * 60)

    # Create test instance
    test = TestScale()
    test.setup_method(
        active_cluster_name='scale-test-active',
        terminated_cluster_name='scale-test-terminated',
        managed_job_id=1  # Not used
    )

    try:
        # Inject clusters
        injected = test.inject_clusters(count)
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
