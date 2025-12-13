"""Verda Cloud adaptor."""

from json import load as json_load
import os
from typing import TYPE_CHECKING

from sky.adaptors import common

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Verda Cloud. '
                         'Try pip install "skypilot[verda]"')

verda = common.LazyImport('verda', import_error_message=_IMPORT_ERROR_MESSAGE)

if TYPE_CHECKING:
    from verda import VerdaClient

_verda_client = None


def verda_client() -> 'VerdaClient':
    """Return the configured Verda Client.
    See https://github.com/verda-cloud/sdk-python for more details.
    """
    global _verda_client

    if _verda_client is None:
        configured, error, config = get_configuration()
        if not configured:
            raise Exception(error)

        # LazyImport will handle the ImportError with the appropriate message
        _verda_client = verda.VerdaClient(
            client_id=config['client_id'],
            client_secret=config['client_secret'],
            base_url=(config['base_url']
                      if 'base_url' in config else 'https://api.verda.com/v1'),
            inference_key=config.get('inference_key', None),
        )
    return _verda_client


def get_configuration():
    """Checks known config file exists and is valid.
    Also supports using env vars instead.
    """
    try:
        if ('VERDA_CLIENT_ID' in os.environ and
                'VERDA_CLIENT_SECRET' in os.environ):
            # Configured via new env vars
            return (
                True,
                None,
                {
                    'client_id': os.environ['VERDA_CLIENT_ID'],
                    'client_secret': os.environ['VERDA_CLIENT_SECRET'],
                    'base_url': os.environ.get('VERDA_BASE_URL',
                                               'https://api.datacrunch.io/v1'),
                    'default_region': os.environ.get('VERDA_DEFAULT_REGION',
                                                     'FIN-03'),
                    'inference_key': os.environ.get('VERDA_INFERENCE_KEY',
                                                    None),
                },
            )

        if ('DATACRUNCH_CLIENT_ID' in os.environ and
                'DATACRUNCH_CLIENT_SECRET' in os.environ):
            # Configured via old env vars
            return (
                True,
                None,
                {
                    'client_id': os.environ['DATACRUNCH_CLIENT_ID'],
                    'client_secret': os.environ['DATACRUNCH_CLIENT_SECRET'],
                    'base_url': os.environ.get('DATACRUNCH_BASE_URL',
                                               'https://api.datacrunch.io/v1'),
                    'default_region': os.environ.get(
                        'DATACRUNCH_DEFAULT_REGION', 'FIN-03'),
                    'inference_key': os.environ.get('DATACRUNCH_INFERENCE_KEY',
                                                    None),
                },
            )

        filename = '~/.verda/config.json'
        config_file_path = os.path.expanduser(filename)
        if not os.path.exists(config_file_path):
            return (
                False,
                (f'Verda Cloud configuration not found. '
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
                 '        EOF'),
                {},
            )

        # Try to read the API key
        with open(config_file_path, 'r', encoding='utf-8') as f:
            config = json_load(f)

        if 'client_id' not in config or not config.get('client_id'):
            return (
                False,
                ('Verda Cloud Client ID is missing or empty in '
                 f'{config_file_path}\n'
                 '    Please ensure your config.json contains:\n'
                 '        {\n'
                 '          "client_id": "your-api-key",\n'
                 '          "client_secret": "your-api-secret"\n'
                 '        }'),
                {},
            )

        if 'client_secret' not in config or not config.get('client_secret'):
            return (
                False,
                ('Verda Cloud Client Secret is missing or empty in '
                 f'{config_file_path}\n'
                 '    Please ensure your config.json contains:\n'
                 '        {\n'
                 '          "client_id": "your-api-key",\n'
                 '          "client_secret": "your-api-secret"\n'
                 '        }'),
                {},
            )
        return True, None, config

    except (OSError, IOError) as e:
        return (
            False,
            ('Error reading Verda Cloud credentials from '
             f'{config_file_path}: {str(e)}\n'
             '    Please ensure the file exists and is readable.'),
            {},
        )
    except (KeyError, ValueError) as e:
        # KeyError for missing keys, ValueError for JSON decode errors
        return (
            False,
            (f'Error parsing Verda Cloud credentials from '
             f'{config_file_path}: {str(e)}\n'
             '    Please ensure your config.json is valid JSON'),
            {},
        )
