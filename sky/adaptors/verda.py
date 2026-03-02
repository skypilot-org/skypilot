"""Verda Cloud adaptor."""

from json import load as json_load
import os


class VerdaConfiguration:
    """A configuration for the Verda Cloud API."""
    client_id: str
    client_secret: str
    base_url: str
    default_region: str

    def __init__(self, client_id: str, client_secret: str, base_url: str,
                 default_region: str) -> None:
        """Initialize the Verda configuration."""
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url
        self.default_region = default_region


def get_verda_configuration():
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
                VerdaConfiguration(
                    client_id=os.environ['VERDA_CLIENT_ID'],
                    client_secret=os.environ['VERDA_CLIENT_SECRET'],
                    base_url=os.environ.get('VERDA_BASE_URL',
                                            'https://api.verda.com/v1'),
                    default_region=os.environ.get('VERDA_DEFAULT_REGION',
                                                  'FIN-03'),
                ),
            )

        if ('DATACRUNCH_CLIENT_ID' in os.environ and
                'DATACRUNCH_CLIENT_SECRET' in os.environ):
            # Configured via old env vars
            return (
                True,
                None,
                VerdaConfiguration(
                    client_id=os.environ['DATACRUNCH_CLIENT_ID'],
                    client_secret=os.environ['DATACRUNCH_CLIENT_SECRET'],
                    base_url=os.environ.get('DATACRUNCH_BASE_URL',
                                            'https://api.verda.com/v1'),
                    default_region=os.environ.get('DATACRUNCH_DEFAULT_REGION',
                                                  'FIN-03'),
                ),
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
                 '          "base_url": "https://api.verda.com/v1",\n'
                 '          "default_region": "FIN-03"\n'
                 '        }\n'
                 '        EOF'),
                None,
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
                    None,
                )
            elif 'client_secret' not in config or not config.get(
                    'client_secret'):
                return (
                    False,
                    ('Verda Cloud Client Secret is missing or empty in '
                     f'{config_file_path}\n'
                     '    Please ensure your config.json contains:\n'
                     '        {\n'
                     '          "client_id": "your-api-key",\n'
                     '          "client_secret": "your-api-secret"\n'
                     '        }'),
                    None,
                )
            else:
                return True, None, VerdaConfiguration(
                    client_id=config.get('client_id'),
                    client_secret=config.get('client_secret'),
                    base_url=config.get('base_url', 'https://api.verda.com/v1'),
                    default_region=config.get('default_region', 'FIN-03'),
                ),

    except (OSError, IOError) as e:
        return (
            False,
            ('Error reading Verda Cloud credentials from '
             f'{config_file_path}: {str(e)}\n'
             '    Please ensure the file exists and is readable.'),
            None,
        )
    except (KeyError, ValueError) as e:
        # KeyError for missing keys, ValueError for JSON decode errors
        return (
            False,
            (f'Error parsing Verda Cloud credentials from '
             f'{config_file_path}: {str(e)}\n'
             '    Please ensure your config.json is valid JSON'),
            None,
        )
