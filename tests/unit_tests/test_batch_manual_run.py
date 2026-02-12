"""Manual test runner for batch tests (when pytest is not available).

This script manually runs a subset of the batch tests to verify they work.
For full test suite, install pytest and run: pytest tests/unit_tests/test_batch_*.py
"""
import sys
import traceback

import sky.batch
from sky.batch import utils


def run_test(name, test_func):
    """Run a single test function."""
    try:
        test_func()
        print(f'  ✓ {name}')
        return True
    except Exception as e:
        print(f'  ✗ {name}')
        print(f'    Error: {e}')
        traceback.print_exc()
        return False


def test_basic_serialization():
    """Test basic function serialization."""

    def sample():
        import sky.batch
        for batch in sky.batch.load():
            sky.batch.save_results(batch)

    serialized = utils.serialize_function(sample)
    assert isinstance(serialized, str)
    assert len(serialized) > 0


def test_deserialization():
    """Test function deserialization."""

    def sample():
        import sky.batch
        for batch in sky.batch.load():
            sky.batch.save_results(batch)

    serialized = utils.serialize_function(sample)
    deserialized = utils.deserialize_function(serialized)
    assert callable(deserialized)
    assert deserialized.__name__ == 'sample'


def test_valid_function():
    """Test that valid functions pass validation."""

    @sky.batch.remote_function
    def valid():
        for batch in sky.batch.load():
            results = [{'out': item['x'] * 2} for item in batch]
            sky.batch.save_results(results)

    assert sky.batch.remote.is_remote_function(valid)


def test_closure_rejection():
    """Test that closures are rejected."""
    external_var = 42

    try:

        @sky.batch.remote_function
        def invalid():
            for batch in sky.batch.load():
                results = [{
                    'value': item['x'] * external_var
                } for item in batch]
                sky.batch.save_results(results)

        raise AssertionError('Should have raised ValueError')
    except ValueError as e:
        assert 'external_var' in str(e)


def test_internal_import():
    """Test that internal imports are allowed."""

    @sky.batch.remote_function
    def valid():
        import json
        for batch in sky.batch.load():
            results = [json.loads(item['data']) for item in batch]
            sky.batch.save_results(results)

    assert sky.batch.remote.is_remote_function(valid)


def main():
    """Run all manual tests."""
    print('=' * 70)
    print('MANUAL BATCH TESTS')
    print('=' * 70)
    print()

    tests = [
        ('Basic serialization', test_basic_serialization),
        ('Deserialization', test_deserialization),
        ('Valid function', test_valid_function),
        ('Closure rejection', test_closure_rejection),
        ('Internal imports', test_internal_import),
    ]

    print('Running tests...')
    print()

    results = []
    for name, test_func in tests:
        results.append(run_test(name, test_func))

    print()
    print('=' * 70)
    passed = sum(results)
    total = len(results)
    print(f'RESULTS: {passed}/{total} tests passed')
    print('=' * 70)

    if passed == total:
        print('✅ All tests passed!')
        return 0
    else:
        print('❌ Some tests failed')
        return 1


if __name__ == '__main__':
    sys.exit(main())
