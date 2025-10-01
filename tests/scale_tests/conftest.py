#!/usr/bin/env python3
"""
Pytest configuration and fixtures for scale tests.
"""

import os
import sys
import time

import pytest

# Add SkyPilot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def pytest_addoption(parser):
    """Add custom command-line options for scale tests."""
    parser.addoption("--active-cluster",
                     action="store",
                     default="scale-test-active",
                     help="Name of the active cluster to use as template")
    parser.addoption("--terminated-cluster",
                     action="store",
                     default="scale-test-terminated",
                     help="Name of the terminated cluster to use as template")
    parser.addoption("--managed-job-id",
                     action="store",
                     type=int,
                     default=1,
                     help="Job ID of the managed job to use as template")


@pytest.fixture(scope="function", autouse=True)
def setup_test_class(request):
    """Automatically setup test class with sample data parameters."""
    if hasattr(request.instance, 'setup_method'):
        active_cluster = request.config.getoption("--active-cluster")
        terminated_cluster = request.config.getoption("--terminated-cluster")
        managed_job_id = request.config.getoption("--managed-job-id")

        request.instance.setup_method(
            active_cluster_name=active_cluster,
            terminated_cluster_name=terminated_cluster,
            managed_job_id=managed_job_id)


@pytest.fixture(scope="function")
def backup_databases():
    """Create database backups before tests and provide cleanup function."""
    state_db = os.path.expanduser("~/.sky/state.db")
    spot_db = os.path.expanduser("~/.sky/spot_jobs.db")

    backups_created = []

    # Create backups
    if os.path.exists(state_db):
        backup_path = f"{state_db}.backup.{int(time.time())}"
        os.system(f"cp '{state_db}' '{backup_path}'")
        backups_created.append(backup_path)
        print(f"Created backup: {backup_path}")

    if os.path.exists(spot_db):
        backup_path = f"{spot_db}.backup.{int(time.time())}"
        os.system(f"cp '{spot_db}' '{backup_path}'")
        backups_created.append(backup_path)
        print(f"Created backup: {backup_path}")

    yield backups_created

    # Cleanup is handled by individual test teardown


@pytest.fixture(scope="function")
def performance_logger():
    """Logger for performance results."""
    results = {}

    def log_performance(test_name: str, operation: str, count: int,
                        duration: float):
        """Log a performance measurement."""
        if test_name not in results:
            results[test_name] = {}
        results[test_name][operation] = {
            'count': count,
            'duration': duration,
            'rate': count / duration if duration > 0 else 0
        }

    yield log_performance

    # Print summary at end
    print("\n" + "=" * 60)
    print("SCALE TEST PERFORMANCE SUMMARY")
    print("=" * 60)
    for test_name, operations in results.items():
        print(f"\n{test_name}:")
        for op_name, metrics in operations.items():
            print(f"  {op_name}:")
            print(f"    Count: {metrics['count']:,}")
            print(f"    Duration: {metrics['duration']:.3f}s")
            print(f"    Rate: {metrics['rate']:.0f} items/sec")
