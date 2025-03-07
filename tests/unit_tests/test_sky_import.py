"""Ensure the lazy import of modules is not reverted.

This test ensures that the lazy import of modules is not reverted.
"""
import gc
import sys

import pytest

lazy_import_modules = [
    'requests.packages.urllib3.util.retry',
    'requests.adapters',
    'numpy',
    'pendulum',
    'requests',
    'jsonschema',
    'psutil',
    'jinja2',
    'yaml',
    'rich.console',
    'rich.progress',
    'networkx',
    'httpx',
    'pydantic',
]


def test_sky_import():
    # Import sky
    try:
        import sky
    except Exception as e:
        print(f"Failed to import sky: {e}")
        raise

    # Get the lazy imported modules
    lazy_module_names = [
        obj._module_name if hasattr(obj, '_module_name') else None
        for obj in gc.get_objects()
        if isinstance(obj, sky.adaptors.common.LazyImport)
    ]

    # Check that the lazy imported modules are in the list
    for module in lazy_import_modules:
        assert module in lazy_module_names, f"Module {module} is not lazy imported"
