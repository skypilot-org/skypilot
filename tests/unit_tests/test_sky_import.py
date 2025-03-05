"""Ensure the lazy import of modules is not reverted.

This test ensures that the lazy import of modules is not reverted.
"""
import sys

import pytest


def test_sky_import():
    # Clean modules that are lazy imported by sky
    modules_to_clean = [
        module for module in list(sys.modules.keys()) if any(
            module.startswith(prefix) for prefix in [
                'numpy',
                'pendulum',
                'cryptography',
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
            ])
    ]

    for module in modules_to_clean:
        del sys.modules[module]

    # Import sky
    try:
        import sky
    except Exception as e:
        print(f"Failed to import sky: {e}")
        raise

    # Check that the lazy imported modules are not imported
    assert 'requests.packages.urllib3.util.retry' not in sys.modules
    assert 'requests.adapters' not in sys.modules
    assert 'requests' not in sys.modules
    assert 'yaml' not in sys.modules
    assert 'cryptography.hazmat.backends' not in sys.modules
    assert 'cryptography.hazmat.primitives' not in sys.modules
    assert 'cryptography.hazmat.primitives.asymmetric' not in sys.modules
    assert 'rich.progress' not in sys.modules
    assert 'rich.console' not in sys.modules
    assert 'httpx' not in sys.modules
    assert 'psutil' not in sys.modules
    assert 'networkx' not in sys.modules
    assert 'numpy' not in sys.modules
    assert 'jinja2' not in sys.modules
    assert 'pydantic' not in sys.modules
    assert 'jsonschema' not in sys.modules
    assert 'pendulum' not in sys.modules

    # Check that the lazy imported modules are imported after used
    from sky.utils import console_utils
    console_utils.get_console()
    assert 'rich.console' in sys.modules

    from sky.backends import backend_utils
    backend_utils.check_network_connection()
    assert 'requests.packages.urllib3.util.retry' in sys.modules
    assert 'requests.adapters' in sys.modules
    assert 'requests' in sys.modules

    # Skip cryptography tests with Python < 3.9 for the error:
    # ImportError: PyO3 modules compiled for CPython 3.8 or older
    # may only be initialized once per interpreter process
    if sys.version_info >= (3, 9):
        from sky.authentication import _generate_rsa_key_pair
        _generate_rsa_key_pair()
        assert 'cryptography.hazmat.backends' in sys.modules
        assert 'cryptography.hazmat.primitives' in sys.modules
        assert 'cryptography.hazmat.primitives.asymmetric' in sys.modules

    from sky.optimizer import _is_dag_resources_ordered
    _is_dag_resources_ordered(sky.Dag())
    assert 'networkx' in sys.modules

    from sky.provision.kubernetes import network_utils
    network_utils.fill_loadbalancer_template('test', 'test', [80], 'test',
                                             'test')
    assert 'jinja2' in sys.modules
    assert 'yaml' in sys.modules

    from sky.utils import common_utils
    common_utils.validate_schema({}, {})
    assert 'jsonschema' in sys.modules

    from sky.utils import log_utils
    log_utils.readable_time_duration(1, 1)
    assert 'pendulum' in sys.modules
