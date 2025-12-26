"""Verda Cloud (formerly DataCrunch) library wrapper for SkyPilot."""

import json
import time
from typing import List, Optional


import requests

from sky.adaptors.verda import get_verda_configuration
from sky.catalog import common as catalog_common

TOKEN_ENDPOINT = '/oauth2/token'
CLIENT_CREDENTIALS = 'client_credentials'
REFRESH_TOKEN = 'refresh_token'


def get_verda_instance_type(instance_type: str) -> Optional[str]:
    df = catalog_common.read_catalog('verda/vms.csv')
    lookup_dict = df.set_index('InstanceType')['UpstreamCloudId'].to_dict()
    verda_instance_type = lookup_dict.get(instance_type)
    if verda_instance_type is None:
        raise ValueError(
            f'Verda instance type {instance_type} not found in the catalog')
    return verda_instance_type


class APIException(Exception):
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
        data = json.loads(response.text)
        code = data['code'] if 'code' in data else 'Unknown'
        message = data['message'] if 'message' in data else 'Internal error'
        raise APIException(code, message)


class AuthenticationService:
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
        url = self._base_url + TOKEN_ENDPOINT
        payload = {
            'grant_type': CLIENT_CREDENTIALS,
            'client_id': self._client_id,
            'client_secret': self._client_secret,
        }

        response = requests.post(url,
                                 json=payload,
                                 headers=self._generate_headers(),
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
        """Authenticate the client using the refresh token - refresh the access token.

        updates the object's tokens, and:
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
        url = self._base_url + TOKEN_ENDPOINT

        payload = {
            'grant_type': REFRESH_TOKEN,
            'refresh_token': self._refresh_token
        }

        response = requests.post(url,
                                 json=payload,
                                 headers=self._generate_headers())

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

    def _generate_headers(self):
        # get the first 10 chars of the client id
        client_id_truncated = self._client_id[:10]
        headers = {'User-Agent': 'datacrunch-python-' + client_id_truncated}
        return headers

    def is_expired(self) -> bool:
        """Returns true if the access token is expired.

        :return: True if the access token is expired, otherwise False.
        :rtype: bool
        """
        return time.time() >= self._expires_at


class HTTPClient:
    """An http client, a wrapper for the requests library.

    For each request, it adds the authentication header with an access token.
    If the access token is expired it refreshes it before calling the specified API endpoint.
    Also checks the response status code and raises an exception if needed.
    """

    def __init__(self) -> None:
        configured, reason, config = get_verda_configuration()
        if not configured or not config:
            raise RuntimeError(f"Can't connect to Verda Cloud: {reason}")
        self._base_url = config.base_url
        self._auth_service = AuthenticationService(
            config.client_id,
            config.client_secret,
            config.base_url
        )
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
        :param json: A JSON serializable Python object to send in the body of the Request, defaults to None
        :type json: dict, optional
        :param params: Dictionary of querystring data to attach to the Request, defaults to None
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
        :param json: A JSON serializable Python object to send in the body of the Request, defaults to None
        :type json: dict, optional
        :param params: Dictionary of querystring data to attach to the Request, defaults to None
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
        :param params: Dictionary of querystring data to attach to the Request, defaults to None
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

    def patch(self,
              url: str,
              body: Optional[dict],
              params: Optional[dict],
              **kwargs) -> requests.Response:
        """Sends a PATCH request.

        A wrapper for the requests.patch method.

        Builds the url, uses custom headers, refresh tokens if needed.

        :param url: relative url of the API endpoint
        :type url: str
        :param json: A JSON serializable Python object to send in the body of the Request, defaults to None
        :type json: dict, optional
        :param params: Dictionary of querystring data to attach to the Request, defaults to None
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
        :param json: A JSON serializable Python object to send in the body of the Request, defaults to None
        :type json: dict, optional
        :param params: Dictionary of querystring data to attach to the Request, defaults to None
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

        Uses the refresh token to refresh, and if the refresh token is also expired, uses the client credentials.

        :raises APIException: an api exception with message and error type code
        """
        if self._auth_service.is_expired():
            # try to refresh. if refresh token has expired, reauthenticate
            try:
                self._auth_service.refresh()
            except Exception:
                self._auth_service.authenticate()

    def _generate_headers(self) -> dict:
        """Generate the default headers for every request.

        :return: dict with request headers
        :rtype: dict
        """
        headers = {
            'Authorization': self._generate_bearer_header(),
            'User-Agent': self._generate_user_agent(),
            'Content-Type': 'application/json',
        }
        return headers

    def _generate_bearer_header(self) -> str:
        """Generate the authorization header Bearer string.

        :return: Authorization header Bearer string
        :rtype: str
        """
        return f'Bearer {self._auth_service._access_token}'

    def _generate_user_agent(self) -> str:
        """Generate the user agent string.

        :return: user agent string
        :rtype: str
        """
        # get the first 10 chars of the client id
        client_id_truncated = self._auth_service._client_id[:10]
        return f'skypilot-python-{client_id_truncated}'

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

    def __init__(self):
        return

class Instance:
    """Instance model class."""

    def __init__(self, data) -> None:
        self.id = data['id']
        self.status = data['status']
        self.ip = data.get('ip')
        self.hostname = data['hostname']
  
class SSHKey:
    """An SSH key model class."""

    def __init__(self, data) -> None:
        """Initialize a new SSH key object.

        :param data: JSON data
        :type id: dict
        """
        self.id = data['id']
        self.name = data.get('name')
        self.public_key = data.get('public_key')

class VerdaClient:
    """A client for the Verda Cloud API."""

    def __init__(self) -> None:
        return None

    def instances_get(self) -> List[Instance]:
        """Get all instances."""
        if self.http_client is None:
            self.http_client = HTTPClient()
        response = self.http_client.get('/instances').json()
        return [Instance(o) for o in response]

    def instance_get(self, id: str) -> Instance:
        """Get instance."""
        if self.http_client is None:
            self.http_client = HTTPClient()
        response = self.http_client.get(f'/instances/{id}').json()
        return Instance(response)

    def ssh_keys_get(self) -> List[SSHKey]:
        """Get all ssh keys."""
        if self.http_client is None:
            self.http_client = HTTPClient()
        response = self.http_client.get('/ssh-keys').json()
        return [SSHKey(o) for o in response]

    def instance_create(self, payload: dict) -> Instance:
        if self.http_client is None:
            self.http_client = HTTPClient()
        instance_id = self.http_client.post('/instances', body=payload).text
        instance = self.instance_get(instance_id)
        return instance

    def instance_action(self, id, action) -> None:
        if self.http_client is None:
            self.http_client = HTTPClient()
        payload = {'id': [id], 'action': action}
        self.http_client.put('/instances', body=payload)
        return None