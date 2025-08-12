"""Seeweb cloud adaptor - versione ultra-semplificata."""


def check_compute_credentials():
    """Checks if the user has access credentials to Seeweb's compute service."""
    try:
        import sys
        if 'ecsapi' in sys.modules:
            import importlib
            ecsapi = importlib.import_module('ecsapi')
        else:
            import ecsapi

        import configparser
        from pathlib import Path

        # Read API key from standard Seeweb configuration file
        parser = configparser.ConfigParser()
        parser.read(Path('~/.seeweb_cloud/seeweb_keys').expanduser())
        api_key = parser['DEFAULT']['api_key'].strip()

        # Test connection by fetching servers list
        # This validates that the API key is working
        client = ecsapi.Api(token=api_key)
        client.fetch_servers()
        return True, None

    except ImportError:
        return False, 'Seeweb dependencies are not installed. Run: pip install ecsapi'
    except FileNotFoundError:
        return False, 'Missing Seeweb API key file ~/.seeweb_cloud/seeweb_keys'
    except KeyError:
        return False, 'Missing api_key in ~/.seeweb_cloud/seeweb_keys'
    except Exception as e:
        return False, f'Unable to authenticate with Seeweb API: {e}'


def check_storage_credentials():
    """Checks if the user has access credentials to Seeweb's storage service."""
    return check_compute_credentials()


def client():
    """Returns an authenticated ecsapi.Api object."""
    import sys
    if 'ecsapi' in sys.modules:
        import importlib
        ecsapi = importlib.import_module('ecsapi')
    else:
        import ecsapi

    import configparser
    from pathlib import Path

    # Create authenticated client using the same credential pattern
    parser = configparser.ConfigParser()
    parser.read(Path('~/.seeweb_cloud/seeweb_keys').expanduser())
    api_key = parser['DEFAULT']['api_key'].strip()

    return ecsapi.Api(token=api_key)
