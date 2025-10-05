#!/usr/bin/env python3
"""
Simple script to inject test cluster history without cleanup for manual verification.
"""

import os
import sys

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from scale_test_utils import TestScale


def main():
    """Inject test cluster history without cleanup."""
    recent_count = 5
    old_count = 5

    print(
        f"Injecting {recent_count + old_count} test cluster history entries (no cleanup)..."
    )
    print(f"  - {recent_count} recent (within 10 days)")
    print(f"  - {old_count} older (15-30 days ago)")
    print("=" * 60)

    # Create test instance
    test = TestScale()
    test.initialize(
        active_cluster_name='scale-test-active',
        terminated_cluster_name='scale-test-terminated',
        managed_job_id=1  # Not used
    )

    try:
        # Inject cluster history
        injected = test.inject_cluster_history(recent_count=recent_count,
                                               old_count=old_count)
        print(f"\nSuccessfully injected {injected} cluster history entries!")
        print(f"Total cluster hashes tracked: {len(test.test_cluster_hashes)}")

        print("\n" + "=" * 60)
        print("Test cluster history injected. You can now test:")
        print("  from sky import global_user_state")
        print("  global_user_state.get_clusters_from_history(days=10)")
        print("  global_user_state.get_clusters_from_history(days=30)")
        print("\nTo clean up these entries later, run:")
        print("  python tests/scale_tests/cleanup_test_cluster_history.py")

    except Exception as e:
        print(f"\nFailed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
