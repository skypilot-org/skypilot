#!/usr/bin/env python3
"""
Simple script to inject test managed jobs without cleanup for manual verification.
"""

import argparse
import os
import sys

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from scale_test_utils import TestScale


def main():
    """Inject test managed jobs without cleanup."""
    parser = argparse.ArgumentParser(
        description='Inject test managed jobs for manual verification')
    parser.add_argument(
        '--managed-job-id',
        type=int,
        default=1,
        help='Job ID of the managed job to use as template (default: 1)')
    parser.add_argument(
        '--count',
        type=int,
        default=10,
        help='Number of test managed jobs to inject (default: 10)')

    args = parser.parse_args()

    print(f"Injecting {args.count} test managed jobs (no cleanup)...")
    print("=" * 60)

    # Create test instance
    test = TestScale()
    test.initialize(
        active_cluster_name='scale-test-active',
        terminated_cluster_name='scale-test-terminated',
        managed_job_id=args.managed_job_id
    )

    try:
        # Inject managed jobs
        injected = test.inject_managed_jobs(args.count)
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
