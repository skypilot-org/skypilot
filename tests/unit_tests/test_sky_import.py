"""Ensure the lazy import of modules is not reverted.

This test ensures that the lazy import of modules is not reverted.
"""
import copy
import gc
import sys

import pytest

from sky.adaptors import common as adaptors_common


@pytest.fixture
def lazy_import_modules():
    """Get list of lazy-imported module names."""
    return [
        obj._module_name
        for obj in gc.get_objects()
        if isinstance(obj, adaptors_common.LazyImport) and
        hasattr(obj, '_module_name')
    ]


@pytest.fixture
def mock_delete_modules(request, monkeypatch):
    """Fixture to mock delete modules."""
    # Get the lazy_import_modules fixture
    lazy_modules = request.getfixturevalue('lazy_import_modules')

    # Create a copy of sys.modules for this test
    test_modules = copy.copy(sys.modules)

    # Clean modules in the copy
    for module in lazy_modules:
        if module in test_modules:
            del test_modules[module]

    # Use monkeypatch to temporarily replace sys.modules for this test only
    monkeypatch.setattr(sys, 'modules', test_modules)

    yield

    # monkeypatch automatically restores sys.modules after the test


def test_sky_import(lazy_import_modules, mock_delete_modules):
    # Import sky
    try:
        import sky
    except Exception as e:
        print(f"Failed to import sky: {e}")
        raise

    for module in lazy_import_modules:
        assert module not in sys.modules, f"Module {module} is not lazy imported"
