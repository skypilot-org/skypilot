"""Verda Cloud adaptor."""

import functools
import os
from json import load as json_load

_verda_sdk = None

def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _verda_sdk

        if _verda_sdk is None:
            try:
                import datacrunch as _verda  # pylint: disable=import-outside-toplevel
                configured, error, config = get_configuration()
                if not configured:
                    raise Exception(error)

                _verda_sdk = _verda.DataCrunchClient(
                    client_id=config['client_id'],
                    client_secret=config['client_secret'],
                    base_url=config['base_url'] if 'base_url' in config else 'https://api.datacrunch.io/v1',
                    inference_key=config['inference_key'] if 'inference_key' in config else None
                )
            except ImportError as e:
                raise ImportError(f'Fail to import dependencies for Verda Cloud: {e}\n'
                                  'Try pip install "skypilot[verda]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def verda():
    """Return the verda package."""
    return _verda_sdk

def get_configuration():
    """Checks known config file exists and is valid. Also supports using env vars instead."""
    try:
        if 'VERDA_CLIENT_ID' in os.environ and 'VERDA_CLIENT_SECRET' in os.environ:
            # Configured via new env vars
            return True, None, {
                'client_id': os.environ['VERDA_CLIENT_ID'],
                'client_secret': os.environ['VERDA_CLIENT_SECRET'],
                'base_url': os.environ.get('VERDA_BASE_URL', 'https://api.datacrunch.io/v1'),
                'default_region': os.environ.get('VERDA_DEFAULT_REGION', 'FIN-03'),
                'inference_key': os.environ.get('VERDA_INFERENCE_KEY', None)
            }

        if 'DATACRUNCH_CLIENT_ID' in os.environ and 'DATACRUNCH_CLIENT_SECRET' in os.environ:
            # Configured via old env vars
            return True, None, {
                'client_id': os.environ['DATACRUNCH_CLIENT_ID'],
                'client_secret': os.environ['DATACRUNCH_CLIENT_SECRET'],
                'base_url': os.environ.get('DATACRUNCH_BASE_URL', 'https://api.datacrunch.io/v1'),
                'default_region': os.environ.get('DATACRUNCH_DEFAULT_REGION', 'FIN-03'),
                'inference_key': os.environ.get('DATACRUNCH_INFERENCE_KEY', None)
            }
        
        filename = '~/.verda/config.json'
        config_file_path = os.path.expanduser(filename)
        if not os.path.exists(config_file_path):
            return False, (
                f'Verda Cloud configuration not found. '
                f'Please save your config.json as {config_file_path}\n'
                '    Credentials can be set up by:\n'
                '        $ mkdir -p ~/.verda\n'
                '        $ cat > ~/.verda/config.json << EOF\n'
                '        {\n'
                '          "client_id": "your-client-id",\n'
                '          "client_secret": "your-client-secret",\n'
                '          "base_url": "https://api.datacrunch.io/v1",\n'
                '          "default_region": "FIN-03",\n'
                '          "inference_key": "your-inference-key"\n'
                '        }\n'
                '        EOF'
            ), {}

        # Try to read the API key
        with open(config_file_path, 'r', encoding='utf-8') as f:
            config = json_load(f)

        if 'client_id' not in config or not config.get('client_id'):
            return False, (
                f'Verda Cloud Client ID is missing or empty in {config_file_path}\n'
                '    Please ensure your config.json contains:\n'
                '        {\n'
                '          "client_id": "your-api-key",\n'
                '          "client_secret": "your-api-secret"\n'
                '        }'
            ), {}

        if 'client_secret' not in config or not config.get('client_secret'):
            return False, (
                f'Verda Cloud Client Secret is missing or empty in {config_file_path}\n'
                '    Please ensure your config.json contains:\n'
                '        {\n'
                '          "client_id": "your-api-key",\n'
                '          "client_secret": "your-api-secret"\n'
                '        }'
            ), {}
        return True, None, config

    except (OSError, IOError) as e:
        return False, (
            f'Error reading Verda Cloud credentials from {config_file_path}: {str(e)}\n'
            '    Please ensure the file exists and is readable.'
        ), {}
    except (KeyError, ValueError) as e:
        # KeyError for missing keys, ValueError for JSON decode errors
        return False, (
            f'Error parsing Verda Cloud credentials from {config_file_path}: {str(e)}\n'
            '    Please ensure your config.json is valid JSON and contains:\n'
            '        {\n'
            '          "client_id": "your-api-key",\n'
            '          "client_secret": "your-api-secret"\n'
            '        }'
        ), {}