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
    try:
        console_utils.get_console()
    except Exception as e:
        print(f"Failed to get console: {e}")
    assert 'rich.console' in sys.modules

    from sky.backends import backend_utils
    try:
        backend_utils.check_network_connection()
    except Exception as e:
        print(f"Failed to check network connection: {e}")
    assert 'requests.packages.urllib3.util.retry' in sys.modules
    assert 'requests.adapters' in sys.modules
    assert 'requests' in sys.modules

    from sky.authentication import _generate_rsa_key_pair
    try:
        _generate_rsa_key_pair()
    except Exception as e:
        print(f"Failed to generate RSA key pair: {e}")
    assert 'cryptography.hazmat.backends' in sys.modules
    assert 'cryptography.hazmat.primitives' in sys.modules
    assert 'cryptography.hazmat.primitives.asymmetric' in sys.modules

    from sky.optimizer import _is_dag_resources_ordered
    try:
        _is_dag_resources_ordered(sky.Dag())
    except Exception as e:
        print(f"Failed to check DAG resources ordered: {e}")
    assert 'networkx' in sys.modules

    from sky.provision.kubernetes import network_utils
    try:
        network_utils.fill_loadbalancer_template('test', 'test', [80], 'test',
                                                 'test')
    except Exception as e:
        print(f"Failed to fill loadbalancer template: {e}")
    assert 'jinja2' in sys.modules
    assert 'yaml' in sys.modules

    from sky.utils import common_utils
    try:
        common_utils.validate_schema({}, {})
    except Exception as e:
        print(f"Failed to validate schema: {e}")
    assert 'jsonschema' in sys.modules

    from sky.utils import log_utils
    try:
        log_utils.readable_time_duration(1, 1)
    except Exception as e:
        print(f"Failed to readable time duration: {e}")
    assert 'pendulum' in sys.modules
