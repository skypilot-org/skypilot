"""Ensure the lazy import of modules is not reverted.

This test ensures that the lazy import of modules is not reverted,
and that heavy modules are not eagerly imported at startup.
"""
import copy
import gc
import os
import subprocess
import sys

import pytest

from sky.adaptors import common as adaptors_common

# Heavy modules that should NEVER be imported at `import sky` startup.
# These are expensive imports (100ms+) that should only load when needed.
# Add heavy modules here to prevent performance regressions.
FORBIDDEN_EAGER_IMPORTS = frozenset({
    # Data/ML libraries
    'pandas',
    # TODO: Add 'numpy' after fixing sky/optimizer.py to use lazy import
    # Cloud provider SDKs (should only load when that cloud is used)
    'kubernetes',
    'boto3',
    'botocore',
    'google',
    'googleapiclient',
    'azure',
    'oci',
    'ibm_vpc',
    'ibm_cloud_sdk_core',
    'ibm_platform_services',
    'ibm_boto3',
    'ibm_botocore',
    'cudo',
    'pydo',
    'runpod',
    'nebius',
})


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


def test_no_heavy_imports_at_startup():
    """Ensure heavy modules (pandas, numpy, etc.) are not imported at startup.

    This test runs in a subprocess to ensure a clean Python environment,
    avoiding any cached imports from previous tests.
    """
    # Build the check script - suppress all logging to avoid polluting stdout
    check_script = f'''
import sys
import os
# Suppress all logging/debug output
os.environ['SKYPILOT_DEBUG'] = '0'
import logging
logging.disable(logging.CRITICAL)

# Import sky
import sky

# Check for forbidden modules
forbidden = {set(FORBIDDEN_EAGER_IMPORTS)!r}
loaded = []
for mod in sys.modules:
    for forbidden_prefix in forbidden:
        if mod == forbidden_prefix or mod.startswith(forbidden_prefix + "."):
            loaded.append(mod)
            break
if loaded:
    print("FORBIDDEN_IMPORTS:" + ",".join(sorted(set(loaded))))
else:
    print("OK")
'''

    # Run with clean environment, suppress debug output
    env = {k: v for k, v in os.environ.items()}
    env['SKYPILOT_DEBUG'] = '0'

    result = subprocess.run([sys.executable, '-c', check_script],
                            capture_output=True,
                            text=True,
                            env=env)

    # Look for FORBIDDEN_IMPORTS anywhere in output (debug logs may precede it)
    stdout = result.stdout
    if 'FORBIDDEN_IMPORTS:' in stdout:
        # Extract the modules list
        for line in stdout.split('\n'):
            if line.startswith('FORBIDDEN_IMPORTS:'):
                loaded_modules = line.split(':', 1)[1]
                pytest.fail(
                    f'Heavy modules imported at startup (should be lazy): '
                    f'{loaded_modules}\n'
                    f'These imports slow down CLI startup time significantly. '
                    f'Use LazyImport from sky.adaptors.common instead.')
