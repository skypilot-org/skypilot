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

from schema_generator import SchemaBasedGenerator


@pytest.fixture(scope="session")
def generator():
    """Create a shared schema generator for all tests."""
    return SchemaBasedGenerator()


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
