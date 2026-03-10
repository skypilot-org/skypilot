"""Verda Cloud adaptor."""

import dataclasses
from json import load as json_load
from json import loads
import os
import time
from typing import List, Optional, Tuple

import requests


@dataclasses.dataclass
class VerdaConfiguration:
    """A configuration for the Verda Cloud API."""
    client_id: str
    client_secret: str
    base_url: str
    default_region: str


def get_verda_configuration(
) -> Tuple[bool, Optional[str], Optional[VerdaConfiguration]]:
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


_TOKEN_ENDPOINT = '/oauth2/token'
_CLIENT_CREDENTIALS = 'client_credentials'
_REFRESH_TOKEN = 'refresh_token'


class VerdaException(Exception):
    """This exception is raised if there was an error from verda's API.

    Could be an invalid input, token etc.

    Raised when an API HTTP call response has a status code >= 400
    """

    def __init__(self, code: str, message: str) -> None:
        """API Exception.

        :param code: error code
        :type code: str
        :param message: error message
        :type message: str
        """
        self.code = code
        """Error code. should be available in VerdaClient.error_codes"""

        self.message = message
        """Error message
        """
        super().__init__(message)

    def __str__(self) -> str:
        msg = ''
        if self.code:
            msg = f'error code: {self.code}\n'

        msg += f'message: {self.message}'
        return msg


def handle_error(response: requests.Response) -> None:
    """Checks for the status code and is response.ok

    :param response: the API call response
    :raises APIException: an api exception with message and error type code
    """
    if not response.ok:
        data = loads(response.text)
        code = data.get('code', 'Unknown')
        message = data.get('message', 'Internal error')
        raise VerdaException(code, message)


class _AuthenticationService:
    """A service for client authentication."""

    def __init__(self, client_id: str, client_secret: str,
                 base_url: str) -> None:
        self._base_url = base_url
        self._client_id = client_id
        self._client_secret = client_secret

    def authenticate(self) -> dict:
        """Authenticate the client and store the access & refresh tokens.

        returns an authentication data dictionary with the following schema:
        {
            "access_token": token str,
            "refresh_token": token str,
            "scope": scope str,
            "token_type": token type str,
            "expires_in": duration until expires in seconds
        }

        :return: authentication data (tokens, scope, token type, expires in)
        :rtype: dict
        """
        url = self._base_url + _TOKEN_ENDPOINT
        payload = {
            'grant_type': _CLIENT_CREDENTIALS,
            'client_id': self._client_id,
            'client_secret': self._client_secret,
        }

        response = requests.post(url,
                                 json=payload,
                                 headers=self.generate_headers(),
                                 timeout=30)
        handle_error(response)

        auth_data = response.json()

        self._access_token = auth_data['access_token']
        self._refresh_token = auth_data['refresh_token']
        self._scope = auth_data['scope']
        self._token_type = auth_data['token_type']
        self._expires_at = time.time() + auth_data['expires_in']
        return auth_data

    def refresh(self) -> dict:
        """Authenticate the client using the refresh token.

        updates the object's tokens and returns an authentication
        data dictionary with the following schema:
        {
            "access_token": token str,
            "refresh_token": token str,
            "scope": scope str,
            "token_type": token type str,
            "expires_in": duration until expires in seconds
        }

        :return: authentication data (tokens, scope, token type, expires in)
        :rtype: dict
        """
        url = self._base_url + _TOKEN_ENDPOINT

        payload = {
            'grant_type': _REFRESH_TOKEN,
            'refresh_token': self._refresh_token
        }

        response = requests.post(url,
                                 json=payload,
                                 headers=self.generate_headers())

        # if refresh token is also expired, authenticate again:
        if response.status_code == 401 or response.status_code == 400:
            return self.authenticate()
        else:
            handle_error(response)

        auth_data = response.json()

        self._access_token = auth_data['access_token']
        self._refresh_token = auth_data['refresh_token']
        self._scope = auth_data['scope']
        self._token_type = auth_data['token_type']
        self._expires_at = time.time() + auth_data['expires_in']

        return auth_data

    def generate_headers(self):
        """Generate the headers for the API request.

        :return: headers for the API request
        :rtype: dict
        """
        client_id_truncated = self._client_id[:10]
        headers = {
            'User-Agent': f'verda-python-v1-skypilot-{client_id_truncated}',
        }

        if hasattr(self, '_access_token') and self._access_token:
            headers['Authorization'] = f'Bearer {self._access_token}'

        return headers

    def is_expired(self) -> bool:
        """Returns true if the access token is expired.

        :return: True if the access token is expired, otherwise False.
        :rtype: bool
        """
        return time.time() >= self._expires_at


class _HTTPClient:
    """An http client, a wrapper for the requests library.

    For each request, it adds the authentication header with an access token.
    If the access token has expired it is refreshed it before calling the API.
    Also checks the response status code and raises an exception if needed.
    """

    def __init__(self) -> None:
        configured, reason, config = get_verda_configuration()
        if not configured or not config:
            raise RuntimeError(f'Can\'t connect to Verda Cloud: {reason}')
        self._base_url = config.base_url
        self._auth_service = _AuthenticationService(config.client_id,
                                                    config.client_secret,
                                                    config.base_url)
        self._auth_service.authenticate()

    def post(self,
             url: str,
             body: Optional[dict] = None,
             params: Optional[dict] = None,
             **kwargs) -> requests.Response:
        """Sends a POST request.

        A wrapper for the requests.post method.

        Builds the url, uses custom headers, refresh tokens if needed.

        :param url: relative url of the API endpoint
        :type url: str
        :param json: Python object to send in the body of the Request
        :type json: dict, optional
        :param params: Dictionary of querystring data to attach to the Request
        :type params: dict, optional

        :raises APIException: an api exception with message and error type code

        :return: Response object
        :rtype: requests.Response
        """
        self._refresh_token_if_expired()

        url = self._add_base_url(url)
        headers = self._generate_headers()

        response = requests.post(url,
                                 json=body,
                                 headers=headers,
                                 params=params,
                                 **kwargs)
        handle_error(response)

        return response

    def put(self,
            url: str,
            body: Optional[dict] = None,
            params: Optional[dict] = None,
            **kwargs) -> requests.Response:
        """Sends a PUT request.

        A wrapper for the requests.put method.

        Builds the url, uses custom headers, refresh tokens if needed.

        :param url: relative url of the API endpoint
        :type url: str
        :param json: Python object to send in the body of the Request
        :type json: dict, optional
        :param params: Dictionary of querystring data to attach to the Request
        :type params: dict, optional

        :raises APIException: an api exception with message and error type code

        :return: Response object
        :rtype: requests.Response
        """
        self._refresh_token_if_expired()

        url = self._add_base_url(url)
        headers = self._generate_headers()

        response = requests.put(url,
                                json=body,
                                headers=headers,
                                params=params,
                                **kwargs)
        handle_error(response)

        return response

    def get(self,
            url: str,
            params: Optional[dict] = None,
            **kwargs) -> requests.Response:
        """Sends a GET request.

        A wrapper for the requests.get method.

        Builds the url, uses custom headers, refresh tokens if needed.

        :param url: relative url of the API endpoint
        :type url: str
        :param params: Dictionary of querystring data to attach to the Request
        :type params: dict, optional

        :raises APIException: an api exception with message and error type code

        :return: Response object
        :rtype: requests.Response
        """
        self._refresh_token_if_expired()

        url = self._add_base_url(url)
        headers = self._generate_headers()

        response = requests.get(url, params=params, headers=headers, **kwargs)
        handle_error(response)

        return response

    def patch(self, url: str, body: Optional[dict], params: Optional[dict],
              **kwargs) -> requests.Response:
        """Sends a PATCH request.

        A wrapper for the requests.patch method.

        Builds the url, uses custom headers, refresh tokens if needed.

        :param url: relative url of the API endpoint
        :type url: str
        :param json: Python object to send in the body of the Request
        :type json: dict, optional
        :param params: Dictionary of querystring data to attach to the Request
        :type params: dict, optional

        :raises APIException: an api exception with message and error type code

        :return: Response object
        :rtype: requests.Response
        """
        self._refresh_token_if_expired()

        url = self._add_base_url(url)
        headers = self._generate_headers()

        response = requests.patch(url,
                                  json=body,
                                  headers=headers,
                                  params=params,
                                  **kwargs)
        handle_error(response)

        return response

    def delete(self,
               url: str,
               body: Optional[dict] = None,
               params: Optional[dict] = None,
               **kwargs) -> requests.Response:
        """Sends a DELETE request.

        A wrapper for the requests.delete method.

        Builds the url, uses custom headers, refresh tokens if needed.

        :param url: relative url of the API endpoint
        :type url: str
        :param json: Python object to send in the body of the Request
        :type json: dict, optional
        :param params: Dictionary of querystring data to attach to the Request
        :type params: dict, optional

        :raises APIException: an api exception with message and error type code

        :return: Response object
        :rtype: requests.Response
        """
        self._refresh_token_if_expired()

        url = self._add_base_url(url)
        headers = self._generate_headers()

        response = requests.delete(url,
                                   headers=headers,
                                   json=body,
                                   params=params,
                                   **kwargs)
        handle_error(response)

        return response

    def _refresh_token_if_expired(self) -> None:
        """Refreshes the access token if it expired.

        Uses the refresh token to refresh, and if the refresh token is also
        expired, uses the client credentials to authenticate again.

        :raises APIException: an api exception with message and error type code
        """
        if self._auth_service.is_expired():
            # try to refresh. if refresh token has expired, reauthenticate
            try:
                self._auth_service.refresh()
            except Exception:  # pylint: disable=broad-except
                self._auth_service.authenticate()

    def _generate_headers(self) -> dict:
        """Generate the default headers for every request.

        :return: dict with request headers
        :rtype: dict
        """
        headers = self._auth_service.generate_headers()
        headers.update({
            'Content-Type': 'application/json',
        })
        return headers

    def _add_base_url(self, url: str) -> str:
        """Adds the base url to the relative url.

        Example:
        if the relative url is '/balance'
        and the base url is 'https://api.verda.com/v1'
        then this method will return 'https://api.verda.com/v1/balance'

        :param url: a relative url path
        :type url: str
        :return: the full url path
        :rtype: str
        """
        return self._base_url + url


class InstanceStatus:
    """Instance status."""

    ORDERED = 'ordered'
    RUNNING = 'running'
    PROVISIONING = 'provisioning'
    OFFLINE = 'offline'
    STARTING_HIBERNATION = 'starting_hibernation'
    HIBERNATING = 'hibernating'
    RESTORING = 'restoring'
    ERROR = 'error'


class Instance:
    """Instance model class."""

    def __init__(self, data) -> None:
        self.instance_id = data['id']
        self.status = data['status']
        self.hostname = data['hostname']
        # For not yet provisioned instances, ip is not available
        self.ip = data.get('ip')


class SSHKey:
    """An SSH key model class."""

    def __init__(self, data) -> None:
        """Initialize a new SSH key object.

        :param data: JSON data
        :type id: dict
        """
        self.id = data['id']
        self.name = data['name']
        self.public_key = data['key']


class VerdaClient:
    """A client for the Verda Cloud API."""

    def __init__(self) -> None:
        self.http_client: Optional[_HTTPClient] = None

    def instances_get(self) -> List[Instance]:
        """Get all instances."""
        if self.http_client is None:
            self.http_client = _HTTPClient()
        response = self.http_client.get('/instances').json()
        return [Instance(o) for o in response]

    def instance_get(self, instance_id: str) -> Instance:
        """Get instance."""
        if self.http_client is None:
            self.http_client = _HTTPClient()
        response = self.http_client.get(f'/instances/{instance_id}').json()
        return Instance(response)

    def ssh_keys_get(self) -> List[SSHKey]:
        """Get all ssh keys."""
        if self.http_client is None:
            self.http_client = _HTTPClient()
        response = self.http_client.get('/ssh-keys').json()
        return [SSHKey(o) for o in response]

    def ssh_keys_create(self, name: str, key: str) -> SSHKey:
        """Create a new ssh key."""
        if self.http_client is None:
            self.http_client = _HTTPClient()
        payload = {'name': name, 'key': key}
        key_id = self.http_client.post('/ssh-keys', body=payload).text
        return SSHKey({'id': key_id, 'name': name, 'key': key})

    def instance_create(self, payload: dict) -> Instance:
        if self.http_client is None:
            self.http_client = _HTTPClient()
        instance_id = self.http_client.post('/instances', body=payload).text
        instance = self.instance_get(instance_id)
        return instance

    def instance_action(self, instance_id: str, action: str) -> None:
        if self.http_client is None:
            self.http_client = _HTTPClient()
        payload = {'id': [instance_id], 'action': action}
        self.http_client.put('/instances', body=payload)
        return None
