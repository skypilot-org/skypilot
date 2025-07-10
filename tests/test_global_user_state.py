import sys
import threading
from unittest import mock

import pytest

import sky
from sky import global_user_state


@pytest.mark.skipif(sys.platform != 'linux', reason='Only test in CI.')
def test_enabled_clouds_empty():
    # In test environment, no cloud should be enabled.
    assert sky.global_user_state.get_cached_enabled_clouds(
        sky.clouds.cloud.CloudCapability.COMPUTE, workspace='default') == []


def test_concurrent_database_initializationd(tmp_path):
    """Test that concurrent database initialization."""
    # Store original state to restore later
    with mock.patch.object(global_user_state, '_SQLALCHEMY_ENGINE', None):

        temp_db_path = tmp_path / 'state.db'
        results = []
        num_threads = 5

        def worker_thread(thread_id):
            """Worker thread that initializes DB and performs operations"""
            try:
                with mock.patch('os.path.expanduser',
                                return_value=str(temp_db_path)):
                    # Force database initialization
                    global_user_state.initialize_and_get_db()

                    # Immediately try to use the database
                    # This should work if tables are properly created
                    global_user_state.get_user(f"test_user_{thread_id}")

                    results.append((thread_id, "SUCCESS", None))
            except Exception as e:
                results.append((thread_id, "FAILED", e))

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker_thread, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10)

        successes = [r for r in results if r[1] == "SUCCESS"]
        failures = [r for r in results if r[1] == "FAILED"]

        print(f"Results: {len(successes)} successes, {len(failures)} failures")
        if failures:
            print("Failures:")
            for thread_id, status, error in failures:
                print(f"  Thread {thread_id}: {error}")

        assert len(failures) == 0, (
            f"Race condition detected: {len(failures)} threads failed. "
            f"This indicates the database initialization has race conditions. "
            f"Failures: {[(f[0], str(f[2])) for f in failures]}")
