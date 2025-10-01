#!/usr/bin/env python3
"""
Simple script to inject test managed jobs without cleanup for manual verification.
"""

import os
import sys

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from scale_test_utils import TestScale


def main():
    """Inject test managed jobs without cleanup."""
    count = 10

    print(f"Injecting {count} test managed jobs (no cleanup)...")
    print("=" * 60)

    # Create test instance
    test = TestScale()
    test.initialize(
        active_cluster_name='scale-test-active',
        terminated_cluster_name='scale-test-terminated',
        managed_job_id=1  # Required - must exist
    )

    try:
        # Inject managed jobs
        injected = test.inject_managed_jobs(count)
        print(f"\nSuccessfully injected {injected} test managed jobs!")
        print(f"Job IDs: {min(test.test_job_ids)} - {max(test.test_job_ids)}")

        print("\n" + "=" * 60)
        print("Test managed jobs injected. You can now run:")
        print("  sky jobs queue")
        print("\nOr test programmatically:")
        print("  from sky.jobs import state as job_state")
        print("  job_state.get_managed_jobs()")
        print("\nTo clean up these jobs later, run:")
        print("  python tests/scale_tests/cleanup_test_managed_jobs.py")

    except Exception as e:
        print(f"\nFailed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
