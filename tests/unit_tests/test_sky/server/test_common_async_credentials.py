"""Tests for the credentials an async client request sends on each hop.

Most tests send real requests to HTTP(S) servers on 127.0.0.1 and look at the
headers each server received. Test host names never reach DNS: the async
client uses a connector resolver that maps every name to 127.0.0.1, and the
sync client, used for comparison, runs with socket.getaddrinfo patched to do
the same.
"""
import asyncio
import contextlib
import datetime
from http import cookiejar
import itertools
import socket
import ssl
import time
from typing import Any, Dict, Iterator, List, NamedTuple, Optional, Set, Tuple

import aiohttp
from aiohttp import abc as aiohttp_abc
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509 import oid
import multidict
import pytest
import requests
import yarl

from sky.server import common
from sky.server import rest

_TOKEN_HEADERS = {'Authorization': 'Bearer sky_test_only_token'}


class _Hop(NamedTuple):
    """What one server received for one request."""
    server: str
    path: str
    cookie: Optional[str]
    authorization: Optional[str]


class _LoopbackResolver(aiohttp_abc.AbstractResolver):
    """Resolves every host name to 127.0.0.1."""

    async def resolve(self,
                      host: str,
                      port: int = 0,
                      family: socket.AddressFamily = socket.AF_INET) -> Any:
        return [{
            'hostname': host,
            'host': '127.0.0.1',
            'port': port,
            'family': socket.AF_INET,
            'proto': 0,
            'flags': socket.AI_NUMERICHOST,
        }]

    async def close(self) -> None:
        pass


@contextlib.contextmanager
def _loopback_getaddrinfo() -> Iterator[None]:
    """Makes socket.getaddrinfo resolve every host name to 127.0.0.1."""

    def getaddrinfo(host, port, *args, **kwargs):
        del host, args, kwargs  # Unused.
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '',
                 ('127.0.0.1', int(port or 0)))]

    original = socket.getaddrinfo
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = original  # type: ignore[assignment]


class _Servers:
    """Loopback servers that record the credentials of every request."""

    def __init__(self) -> None:
        self.hops: List[_Hop] = []
        # (server name, request path) -> redirect target.
        self.redirects: Dict[Tuple[str, str], str] = {}
        # (server name, request path) -> Set-Cookie header of the response.
        self.set_cookies: Dict[Tuple[str, str], str] = {}
        # (server name, request path) -> statuses of the next responses, one
        # per request, before the path answers normally.
        self.statuses: Dict[Tuple[str, str], List[int]] = {}
        self._runners: List[web.AppRunner] = []

    async def start(self,
                    name: str,
                    ssl_context: Optional[ssl.SSLContext] = None) -> int:
        """Starts a server on 127.0.0.1 and returns its port."""

        async def handler(request: web.Request) -> web.Response:
            cookie_headers = request.headers.getall('Cookie', [])
            assert len(cookie_headers) <= 1, cookie_headers
            self.hops.append(
                _Hop(name, request.path,
                     cookie_headers[0] if cookie_headers else None,
                     request.headers.get('Authorization')))
            headers = {}
            set_cookie = self.set_cookies.get((name, request.path))
            if set_cookie is not None:
                headers['Set-Cookie'] = set_cookie
            statuses = self.statuses.get((name, request.path))
            if statuses:
                return web.json_response({},
                                         status=statuses.pop(0),
                                         headers=headers)
            target = self.redirects.get((name, request.path))
            if target is not None:
                headers['Location'] = target
                return web.Response(status=307, headers=headers)
            return web.json_response({}, headers=headers)

        app = web.Application()
        app.router.add_route('*', '/{tail:.*}', handler)
        runner = web.AppRunner(app)
        await runner.setup()
        self._runners.append(runner)
        site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=ssl_context)
        await site.start()
        return runner.addresses[0][1]

    async def close(self) -> None:
        for runner in self._runners:
            await runner.cleanup()


@contextlib.asynccontextmanager
async def _servers():
    servers = _Servers()
    try:
        yield servers
    finally:
        await servers.close()


def _self_signed_ssl_context(tmp_path, host: str) -> ssl.SSLContext:
    """A server-side TLS context with a throwaway self-signed certificate."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(oid.NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(
        name).public_key(key.public_key()).serial_number(
            x509.random_serial_number()).not_valid_before(
                now - datetime.timedelta(minutes=5)).not_valid_after(
                    now + datetime.timedelta(hours=1)).add_extension(
                        x509.SubjectAlternativeName([x509.DNSName(host)]),
                        critical=False).sign(key, hashes.SHA256()))
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM,
                          serialization.PrivateFormat.TraditionalOpenSSL,
                          serialization.NoEncryption()))
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(str(cert_path), str(key_path))
    return ssl_context


def _cookie(name: str,
            value: Optional[str],
            domain: str,
            path: str = '/',
            secure: bool = False,
            expires_in: float = 1000) -> cookiejar.Cookie:
    return cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=domain.startswith('.'),
        domain_initial_dot=domain.startswith('.'),
        path=path,
        path_specified=bool(path),
        secure=secure,
        expires=int(time.time() + expires_in),
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
    )


def _scoped_cookies() -> List[cookiejar.Cookie]:
    """Cookies for two hosts, two paths, HTTPS only, and an expired one."""
    return [
        _cookie('host-cookie', 'for-host', 'api.test'),
        _cookie('secure-cookie', 'for-https', 'api.test', secure=True),
        _cookie('api-cookie', 'for-api', 'api.test', path='/api'),
        _cookie('landing-cookie', 'for-landing', 'api.test', path='/landing'),
        _cookie('other-cookie', 'for-other', 'other.test'),
        _cookie('expired-cookie', 'expired', 'api.test', expires_in=-1000),
    ]


def _jar(cookies: List[cookiejar.Cookie]) -> requests.cookies.RequestsCookieJar:
    cookie_jar = requests.cookies.RequestsCookieJar()
    for cookie in cookies:
        cookie_jar.set_cookie(cookie)
    return cookie_jar


def _in_any_order(*pairs: str) -> Set[str]:
    """Every Cookie header that consists of these `name=value` pairs.

    Cookies with different paths have a fixed order: the more specific path
    comes first. Cookies that share a path are sent in the jar's iteration
    order, which depends on the Python version (sorted by name on 3.9), so a
    test must not expect one particular order for them.
    """
    return {'; '.join(order) for order in itertools.permutations(pairs)}


@pytest.fixture(autouse=True)
def sync_client_without_cookies_of_other_tests():
    """Clears the cookies the sync client collected from responses.

    The sync client sends every request through one `requests.Session` per
    process, which keeps the cookies that responses set. Without this, a
    cookie set in one test would be sent by the sync client in the next.
    """
    rest._session.cookies.clear()  # pylint: disable=protected-access
    yield
    rest._session.cookies.clear()  # pylint: disable=protected-access


@pytest.fixture(autouse=True)
def no_service_account_token(monkeypatch):
    """Cookie authentication unless a test asks for a token."""
    monkeypatch.setattr(
        'sky.client.service_account_auth.get_service_account_headers',
        lambda: {})


@pytest.fixture
def cookie_file(tmp_path, monkeypatch):
    """The client's cookie file, written in the Mozilla format."""
    cookie_path = tmp_path / 'cookies.txt'
    monkeypatch.setattr('sky.server.common.get_api_cookie_jar_path',
                        lambda: cookie_path)

    def write(cookies: List[cookiejar.Cookie]) -> None:
        file_jar = cookiejar.MozillaCookieJar(str(cookie_path))
        for cookie in cookies:
            file_jar.set_cookie(cookie)
        file_jar.save(ignore_discard=True, ignore_expires=True)

    return write


async def _async_hops(servers: _Servers, server_url: str, path: str,
                      **kwargs) -> List[_Hop]:
    """The hops of one request made through the async client."""
    del servers.hops[:]
    connector = aiohttp.TCPConnector(resolver=_LoopbackResolver())
    async with aiohttp.ClientSession(connector=connector) as session:
        response = await common.make_authenticated_request_async(
            session,
            'GET',
            path,
            server_url=server_url,
            retry=False,
            ssl=False,
            **kwargs)
        await response.read()
    return list(servers.hops)


async def _sync_hops(servers: _Servers, server_url: str, path: str,
                     **kwargs) -> List[_Hop]:
    """The hops of the same request made through the sync client."""
    del servers.hops[:]

    def send() -> None:
        with _loopback_getaddrinfo():
            common.make_authenticated_request('GET',
                                              path,
                                              server_url=server_url,
                                              retry=False,
                                              verify=False,
                                              **kwargs)

    await asyncio.get_running_loop().run_in_executor(None, send)
    return list(servers.hops)


async def _hops_of_both_clients(servers: _Servers, server_url: str, path: str,
                                **kwargs) -> List[_Hop]:
    """The async client's hops, after checking the sync client sent the same."""
    async_hops = await _async_hops(servers, server_url, path, **kwargs)
    sync_hops = await _sync_hops(servers, server_url, path, **kwargs)
    assert async_hops == sync_hops
    return async_hops


# Cookies of the first request.


@pytest.mark.asyncio
async def test_first_request_sends_only_cookies_for_its_url(cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('api')

        hops = await _hops_of_both_clients(servers, f'http://api.test:{port}',
                                           '/api/get?request_id=abc')

    # Not the Secure cookie (plain HTTP), nor the cookie of the other path,
    # the other host's cookie or the expired one.
    assert hops == [
        _Hop('api', '/api/get', 'api-cookie=for-api; host-cookie=for-host',
             None)
    ]


@pytest.mark.asyncio
async def test_request_without_applicable_cookie_has_no_cookie_header(
        cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('unrelated')

        hops = await _hops_of_both_clients(servers,
                                           f'http://unrelated.test:{port}',
                                           '/api/get')

    assert hops == [_Hop('unrelated', '/api/get', None, None)]


@pytest.mark.asyncio
async def test_expired_cookie_in_a_jar_is_not_sent():
    # A jar passed by the caller, so that the expired cookie is not already
    # dropped when the cookie file is loaded.
    async with _servers() as servers:
        port = await servers.start('api')

        hops = await _hops_of_both_clients(servers,
                                           f'http://api.test:{port}',
                                           '/api/get',
                                           cookies=_jar(_scoped_cookies()))

    assert hops == [
        _Hop('api', '/api/get', 'api-cookie=for-api; host-cookie=for-host',
             None)
    ]


@pytest.mark.asyncio
async def test_jar_of_the_caller_is_sent_in_the_order_of_the_sync_client():
    # Three cookies with one path, added in an order that is not sorted by
    # name. `requests` looks cookies up in a copy of the jar, and on Python
    # 3.9 a copy holds them sorted by name.
    cookie_jar = _jar([
        _cookie('c-cookie', 'c', 'api.test'),
        _cookie('a-cookie', 'a', 'api.test'),
        _cookie('b-cookie', 'b', 'api.test'),
    ])
    async with _servers() as servers:
        port = await servers.start('api')

        hops = await _hops_of_both_clients(servers,
                                           f'http://api.test:{port}',
                                           '/api/get',
                                           cookies=cookie_jar)

    assert [hop.cookie for hop in hops] != [None]
    assert hops[0].cookie in _in_any_order('a-cookie=a', 'b-cookie=b',
                                           'c-cookie=c')


@pytest.mark.asyncio
async def test_cookie_saved_by_login_is_sent_to_its_host_only(cookie_file):
    # `sky api login` saves cookies with a leading dot on the domain and an
    # empty path.
    cookie_file([
        cookiejar.Cookie(version=0,
                         name='login-session',
                         value='a=|1|b==',
                         port=None,
                         port_specified=False,
                         domain='.api.test',
                         domain_specified=True,
                         domain_initial_dot=False,
                         path='',
                         path_specified=False,
                         secure=False,
                         expires=int(time.time()) + 1000,
                         discard=False,
                         comment=None,
                         comment_url=None,
                         rest={})
    ])
    async with _servers() as servers:
        port = await servers.start('server')

        for host, expected_cookie in (('api.test', 'login-session=a=|1|b=='),
                                      ('sub.api.test',
                                       'login-session=a=|1|b=='),
                                      ('notapi.test', None), ('other.test',
                                                              None)):
            hops = await _hops_of_both_clients(servers, f'http://{host}:{port}',
                                               '/api/get')
            assert hops == [_Hop('server', '/api/get', expected_cookie,
                                 None)], host


@pytest.mark.asyncio
async def test_two_cookies_with_one_name_are_both_sent(cookie_file):
    cookie_file([
        _cookie('session', 'for-root', 'api.test', path='/'),
        _cookie('session', 'for-api', 'api.test', path='/api'),
    ])
    async with _servers() as servers:
        port = await servers.start('api')

        hops = await _hops_of_both_clients(servers, f'http://api.test:{port}',
                                           '/api/get')

    # The more specific path first, as the cookie jar orders them.
    assert hops == [
        _Hop('api', '/api/get', 'session=for-api; session=for-root', None)
    ]


@pytest.mark.asyncio
async def test_cookie_names_and_values_are_sent_unchanged(cookie_file):
    cookie_file([
        _cookie('$session', 'dollar-name', 'api.test'),
        _cookie('two-parts', 'x; two words', 'api.test'),
        _cookie('json', '{"hint":"user@api.test","sub":"abc=="}', 'api.test'),
    ])
    async with _servers() as servers:
        port = await servers.start('api')

        hops = await _hops_of_both_clients(servers, f'http://api.test:{port}',
                                           '/api/get')

    assert [(hop.server, hop.path) for hop in hops] == [('api', '/api/get')]
    assert hops[0].cookie in _in_any_order(
        '$session=dollar-name', 'two-parts=x; two words',
        'json={"hint":"user@api.test","sub":"abc=="}')


@pytest.mark.asyncio
async def test_url_with_user_and_password_matches_the_sync_client(cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('api')

        hops = await _hops_of_both_clients(
            servers, f'http://user:password@api.test:{port}', '/api/get')

    # The cookie policy of the sync client sees `user:password@api.test` as
    # the host, so no cookie applies. Both clients authenticate with the
    # user and password.
    assert [hop.cookie for hop in hops] == [None]
    assert hops[0].authorization.startswith('Basic ')


# Cookies after a redirect.


@pytest.mark.asyncio
async def test_redirect_to_another_host_gets_that_hosts_cookies_only(
        cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        api_port = await servers.start('api')
        other_port = await servers.start('other')
        servers.redirects['api', '/api/get'] = (
            f'http://other.test:{other_port}/landing')

        hops = await _hops_of_both_clients(servers,
                                           f'http://api.test:{api_port}',
                                           '/api/get')

    assert hops == [
        _Hop('api', '/api/get', 'api-cookie=for-api; host-cookie=for-host',
             None),
        _Hop('other', '/landing', 'other-cookie=for-other', None),
    ]


@pytest.mark.asyncio
async def test_redirect_to_a_host_without_cookies_gets_no_cookie(cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        api_port = await servers.start('api')
        unrelated_port = await servers.start('unrelated')
        servers.redirects['api', '/api/get'] = (
            f'http://unrelated.test:{unrelated_port}/api/get')

        hops = await _hops_of_both_clients(servers,
                                           f'http://api.test:{api_port}',
                                           '/api/get')

    assert hops == [
        _Hop('api', '/api/get', 'api-cookie=for-api; host-cookie=for-host',
             None),
        _Hop('unrelated', '/api/get', None, None),
    ]


@pytest.mark.asyncio
async def test_redirect_to_another_path_gets_the_cookies_of_that_path(
        cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('api')
        servers.redirects['api', '/api/get'] = '/landing'

        hops = await _hops_of_both_clients(servers, f'http://api.test:{port}',
                                           '/api/get')

    assert hops == [
        _Hop('api', '/api/get', 'api-cookie=for-api; host-cookie=for-host',
             None),
        _Hop('api', '/landing',
             'landing-cookie=for-landing; host-cookie=for-host', None),
    ]


@pytest.mark.asyncio
async def test_redirect_from_http_to_https_adds_the_secure_cookie(
        cookie_file, tmp_path):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        http_port = await servers.start('api-http')
        https_port = await servers.start(
            'api-https', _self_signed_ssl_context(tmp_path, 'api.test'))
        servers.redirects['api-http', '/api/get'] = (
            f'https://api.test:{https_port}/api/get')

        hops = await _hops_of_both_clients(servers,
                                           f'http://api.test:{http_port}',
                                           '/api/get')

    assert [(hop.server, hop.path) for hop in hops
           ] == [('api-http', '/api/get'), ('api-https', '/api/get')]
    assert hops[0].cookie == 'api-cookie=for-api; host-cookie=for-host'
    # The cookie of the more specific path first, then the two of path `/`.
    assert hops[1].cookie in {
        'api-cookie=for-api; ' + root_cookies for root_cookies in _in_any_order(
            'host-cookie=for-host', 'secure-cookie=for-https')
    }


@pytest.mark.asyncio
async def test_cookie_set_by_a_redirect_is_sent_on_the_next_hop(cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('api')
        servers.redirects['api', '/api/get'] = '/landing'
        # For example the stickiness cookie of a load balancer.
        servers.set_cookies['api', '/api/get'] = 'lb=sticky; Path=/'

        hops = await _hops_of_both_clients(servers, f'http://api.test:{port}',
                                           '/api/get')

    assert [(hop.server, hop.path) for hop in hops] == [('api', '/api/get'),
                                                        ('api', '/landing')]
    assert hops[0].cookie == 'api-cookie=for-api; host-cookie=for-host'
    # The cookie of the more specific path first, then the two of path `/`.
    assert hops[1].cookie in {
        'landing-cookie=for-landing; ' + root_cookies
        for root_cookies in _in_any_order('host-cookie=for-host', 'lb=sticky')
    }


@pytest.mark.asyncio
async def test_cookie_set_by_a_redirect_is_not_sent_to_another_host(
        cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        api_port = await servers.start('api')
        other_port = await servers.start('other')
        servers.redirects['api', '/api/get'] = (
            f'http://other.test:{other_port}/landing')
        servers.set_cookies['api', '/api/get'] = 'lb=sticky; Path=/'

        hops = await _hops_of_both_clients(servers,
                                           f'http://api.test:{api_port}',
                                           '/api/get')

    assert hops == [
        _Hop('api', '/api/get', 'api-cookie=for-api; host-cookie=for-host',
             None),
        _Hop('other', '/landing', 'other-cookie=for-other', None),
    ]


@pytest.mark.asyncio
async def test_cookie_set_by_a_redirect_does_not_reach_the_jar_of_the_caller():
    cookie_jar = _jar(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('api')
        servers.redirects['api', '/api/get'] = '/landing'
        servers.set_cookies['api', '/api/get'] = 'lb=sticky; Path=/'

        hops = await _async_hops(servers,
                                 f'http://api.test:{port}',
                                 '/api/get',
                                 cookies=cookie_jar)

    assert 'lb=sticky' in hops[1].cookie.split('; ')
    assert 'lb' not in [cookie.name for cookie in cookie_jar]


@pytest.mark.asyncio
async def test_relative_redirect_of_an_endpoint_with_user_and_password(
        cookie_file):
    """The cookie policy sees `user:password@` on both hops, as in `requests`.

    The sync client keeps the credentials in the URL across a relative
    redirect. So on the second hop it sends the cookie the redirect set, and
    none of the jar's cookies for the bare host name.
    """
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('api')
        servers.redirects['api', '/api/get'] = '/landing'
        servers.set_cookies['api', '/api/get'] = 'route=one; Path=/'
        url = f'http://user:password@api.test:{port}'

        async_hops = await _async_hops(servers, url, '/api/get')
        sync_hops = await _sync_hops(servers, url, '/api/get')

    assert [(hop.path, hop.cookie) for hop in async_hops
           ] == [(hop.path, hop.cookie) for hop in sync_hops]
    assert [(hop.path, hop.cookie) for hop in async_hops
           ] == [('/api/get', None), ('/landing', 'route=one')]


@pytest.mark.asyncio
async def test_absolute_redirect_of_an_endpoint_with_user_and_password(
        cookie_file):
    """A redirect that names the host again drops `user:password@`."""
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('api')
        servers.redirects['api',
                          '/api/get'] = (f'http://api.test:{port}/landing')
        servers.set_cookies['api', '/api/get'] = 'route=one; Path=/'
        url = f'http://user:password@api.test:{port}'

        async_hops = await _async_hops(servers, url, '/api/get')
        sync_hops = await _sync_hops(servers, url, '/api/get')

    assert [(hop.path, hop.cookie) for hop in async_hops
           ] == [(hop.path, hop.cookie) for hop in sync_hops]
    assert [(hop.path, hop.cookie) for hop in async_hops] == [
        ('/api/get', None),
        ('/landing', 'landing-cookie=for-landing; host-cookie=for-host')
    ]


@pytest.mark.asyncio
async def test_cookie_order_after_two_redirects_matches_the_sync_client():
    """A cookie set by the first redirect, on the third hop.

    `requests` copies the jar for every hop. On Python 3.9 and 3.10 a copy
    sorts the cookies by name, so the new cookie moves to its sorted place
    one hop after it was added.
    """
    cookies = [
        _cookie('mmm', '1', 'api.test'),
        _cookie('zzz', '2', 'api.test'),
        _cookie('bbb', '3', 'api.test'),
    ]
    async with _servers() as servers:
        port = await servers.start('api')
        servers.redirects['api', '/a'] = '/b'
        servers.redirects['api', '/b'] = '/c'
        servers.set_cookies['api', '/a'] = 'aaa=first-hop; Path=/'
        url = f'http://api.test:{port}'

        hops = await _hops_of_both_clients(servers,
                                           url,
                                           '/a',
                                           cookies=_jar(cookies))

    assert [hop.path for hop in hops] == ['/a', '/b', '/c']
    assert set(hops[0].cookie.split('; ')) == {'mmm=1', 'zzz=2', 'bbb=3'}
    for hop in hops[1:]:
        assert set(hop.cookie.split('; ')) == {
            'mmm=1', 'zzz=2', 'bbb=3', 'aaa=first-hop'
        }


# Cookies the caller sets itself.


@pytest.mark.asyncio
async def test_cookie_header_of_the_caller_is_kept_on_the_first_request(
        cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('api')
        servers.redirects['api', '/api/get'] = '/landing'

        hops = await _hops_of_both_clients(servers,
                                           f'http://api.test:{port}',
                                           '/api/get',
                                           headers={'Cookie': 'mine=1'})

    # As in the sync client: the jar adds nothing to a request that already
    # has a Cookie header, and a redirect hop gets the jar's cookies.
    assert hops == [
        _Hop('api', '/api/get', 'mine=1', None),
        _Hop('api', '/landing',
             'landing-cookie=for-landing; host-cookie=for-host', None),
    ]


@pytest.mark.asyncio
async def test_cookie_header_of_the_caller_is_kept_when_the_request_is_retried(
        cookie_file, monkeypatch):
    """A redirect that ends in a 503 is retried from the first request."""
    monkeypatch.setattr('sky.utils.common_utils.Backoff.current_backoff',
                        lambda self: 0)
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('api')
        servers.redirects['api', '/api/get'] = '/landing'
        servers.statuses['api', '/landing'] = [503]

        connector = aiohttp.TCPConnector(resolver=_LoopbackResolver())
        async with aiohttp.ClientSession(connector=connector) as session:
            response = await common.make_authenticated_request_async(
                session,
                'GET',
                '/api/get',
                server_url=f'http://api.test:{port}',
                retry=True,
                headers={'Cookie': 'mine=1'})
            await response.read()

    first_attempt = [
        _Hop('api', '/api/get', 'mine=1', None),
        _Hop('api', '/landing',
             'landing-cookie=for-landing; host-cookie=for-host', None),
    ]
    assert response.status == 200
    assert servers.hops == first_attempt + first_attempt


@pytest.mark.asyncio
async def test_cookie_header_of_the_caller_is_not_sent_to_another_host(
        cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        api_port = await servers.start('api')
        unrelated_port = await servers.start('unrelated')
        servers.redirects['api', '/api/get'] = (
            f'http://unrelated.test:{unrelated_port}/api/get')

        hops = await _hops_of_both_clients(servers,
                                           f'http://api.test:{api_port}',
                                           '/api/get',
                                           headers={'Cookie': 'mine=1'})

    assert hops == [
        _Hop('api', '/api/get', 'mine=1', None),
        _Hop('unrelated', '/api/get', None, None),
    ]


@pytest.mark.asyncio
async def test_cookies_argument_of_the_caller_is_left_to_aiohttp(cookie_file):
    cookie_file(_scoped_cookies())
    async with _servers() as servers:
        port = await servers.start('api')

        hops = await _async_hops(servers,
                                 f'http://api.test:{port}',
                                 '/api/get',
                                 cookies={'mine': '1'})

    assert hops == [_Hop('api', '/api/get', 'mine=1', None)]


@pytest.mark.asyncio
async def test_middlewares_of_the_session_still_run(cookie_file):
    cookie_file(_scoped_cookies())
    seen_urls = []

    async def session_middleware(request, handler):
        seen_urls.append(str(request.url))
        return await handler(request)

    async with _servers() as servers:
        port = await servers.start('api')
        connector = aiohttp.TCPConnector(resolver=_LoopbackResolver())
        async with aiohttp.ClientSession(
                connector=connector,
                middlewares=(session_middleware,)) as session:
            response = await common.make_authenticated_request_async(
                session,
                'GET',
                '/api/get',
                server_url=f'http://api.test:{port}',
                retry=False)
            await response.read()
        hops = list(servers.hops)

    assert seen_urls == [f'http://api.test:{port}/api/get']
    assert hops == [
        _Hop('api', '/api/get', 'api-cookie=for-api; host-cookie=for-host',
             None)
    ]


# The service account token.


@pytest.mark.asyncio
async def test_token_is_not_sent_to_another_host_after_a_redirect(
        cookie_file, monkeypatch):
    cookie_file(_scoped_cookies())
    monkeypatch.setattr(
        'sky.client.service_account_auth.get_service_account_headers',
        lambda: dict(_TOKEN_HEADERS))
    async with _servers() as servers:
        api_port = await servers.start('api')
        other_port = await servers.start('other')
        servers.redirects['api', '/api/get'] = (
            f'http://other.test:{other_port}/landing')

        hops = await _hops_of_both_clients(servers,
                                           f'http://api.test:{api_port}',
                                           '/api/get')

    # With a token no cookie is sent at all.
    assert hops == [
        _Hop('api', '/api/get', None, _TOKEN_HEADERS['Authorization']),
        _Hop('other', '/landing', None, None),
    ]


@pytest.mark.asyncio
async def test_token_is_kept_for_a_redirect_on_the_same_origin(monkeypatch):
    monkeypatch.setattr(
        'sky.client.service_account_auth.get_service_account_headers',
        lambda: dict(_TOKEN_HEADERS))
    async with _servers() as servers:
        port = await servers.start('api')
        servers.redirects['api', '/api/get'] = '/landing'

        hops = await _hops_of_both_clients(servers, f'http://api.test:{port}',
                                           '/api/get')

    assert hops == [
        _Hop('api', '/api/get', None, _TOKEN_HEADERS['Authorization']),
        _Hop('api', '/landing', None, _TOKEN_HEADERS['Authorization']),
    ]


# The middleware on its own, for what the wire tests cannot reach: a hop that
# still carries an Authorization header or a Cookie header.


class _FakeRequest:
    """The two attributes of aiohttp.ClientRequest the middleware uses."""

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None):
        self.url = yarl.URL(url)
        self.headers = multidict.CIMultiDict(headers or {})


class _FakeResponse:
    """The two attributes of aiohttp.ClientResponse the middleware uses."""

    def __init__(self,
                 status: int = 200,
                 headers: Optional[Dict[str, str]] = None):
        self.status = status
        self.headers = multidict.CIMultiDict(headers or {})


async def _headers_after_middleware(
        middleware,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        response: Optional[_FakeResponse] = None) -> Dict[str, str]:
    request = _FakeRequest(url, headers)
    response = response or _FakeResponse()

    async def handler(handled_request):
        assert handled_request is request
        return response

    assert await middleware(request, handler) is response
    return dict(request.headers)


@pytest.mark.asyncio
async def test_middleware_removes_authorization_from_another_origin():
    first_url = 'http://api.test/api/get'
    middleware = common._credentials_per_hop_middleware(first_url, None)

    assert await _headers_after_middleware(
        middleware, first_url, dict(_TOKEN_HEADERS)) == (_TOKEN_HEADERS)
    assert await _headers_after_middleware(
        middleware, 'http://api.test/landing',
        dict(_TOKEN_HEADERS)) == (_TOKEN_HEADERS)
    # Another host, port or scheme. That includes the upgrade to https:
    # aiohttp removes the header on that hop itself.
    for other_origin in ('http://other.test/api/get',
                         'http://api.test:8080/api/get',
                         'https://api.test/api/get',
                         'https://api.test:8443/api/get'):
        assert await _headers_after_middleware(middleware, other_origin,
                                               dict(_TOKEN_HEADERS)) == {}


@pytest.mark.asyncio
async def test_middleware_replaces_a_cookie_header_carried_to_a_redirect_hop():
    first_url = 'http://api.test/api/get'
    middleware = common._credentials_per_hop_middleware(first_url,
                                                        _jar(_scoped_cookies()))

    assert await _headers_after_middleware(middleware, first_url) == {
        'Cookie': 'api-cookie=for-api; host-cookie=for-host'
    }
    # Whatever Cookie header a hop arrives with is replaced by the jar's
    # cookies for that hop, or removed.
    assert await _headers_after_middleware(
        middleware, 'http://other.test/landing',
        {'Cookie': 'api-cookie=for-api; host-cookie=for-host'}) == {
            'Cookie': 'other-cookie=for-other'
        }
    assert await _headers_after_middleware(
        middleware, 'http://unrelated.test/landing',
        {'Cookie': 'api-cookie=for-api; host-cookie=for-host'}) == {}


@pytest.mark.asyncio
async def test_middleware_starts_over_when_the_request_is_sent_again():
    first_url = 'http://api.test/api/get'
    middleware = common._credentials_per_hop_middleware(
        first_url, _jar(_scoped_cookies()), keep_first_cookie_header=True)
    redirect = _FakeResponse(307, {
        'Location': '/landing',
        'Set-Cookie': 'route=one; Path=/'
    })

    # First attempt: a redirect whose target is not available.
    assert await _headers_after_middleware(middleware, first_url,
                                           {'Cookie': 'mine=1'}, redirect) == {
                                               'Cookie': 'mine=1'
                                           }
    landing = await _headers_after_middleware(middleware,
                                              'http://api.test/landing',
                                              {'Cookie': 'mine=1'},
                                              _FakeResponse(503))
    assert set(landing['Cookie'].split('; ')) == {
        'landing-cookie=for-landing', 'host-cookie=for-host', 'route=one'
    }
    # The caller sends the request again through the same middleware: it is
    # a first request again, from the jar of the caller.
    assert await _headers_after_middleware(
        middleware, first_url, {'Cookie': 'mine=1'},
        _FakeResponse(307, {'Location': '/landing'})) == {
            'Cookie': 'mine=1'
        }
    landing = await _headers_after_middleware(middleware,
                                              'http://api.test/landing',
                                              {'Cookie': 'mine=1'})
    assert set(landing['Cookie'].split('; ')) == {
        'landing-cookie=for-landing', 'host-cookie=for-host'
    }


@pytest.mark.asyncio
async def test_middleware_follows_a_redirect_named_by_a_uri_header():
    """aiohttp follows a redirect that has a URI header and no Location.

    When it points back at the same URL, the next request is a hop of that
    redirect, not the request sent again: it gets the cookie the response
    set, and a Cookie header of the caller is not kept for it.
    """
    first_url = 'http://api.test/api/get'
    middleware = common._credentials_per_hop_middleware(
        first_url, _jar(_scoped_cookies()), keep_first_cookie_header=True)

    assert await _headers_after_middleware(
        middleware, first_url, {'Cookie': 'mine=1'},
        _FakeResponse(307, {
            'URI': '/api/get',
            'Set-Cookie': 'route=one; Path=/'
        })) == {
            'Cookie': 'mine=1'
        }
    again = await _headers_after_middleware(middleware, first_url,
                                            {'Cookie': 'mine=1'})
    assert set(again['Cookie'].split('; ')) == {
        'api-cookie=for-api', 'host-cookie=for-host', 'route=one'
    }


@pytest.mark.asyncio
async def test_middleware_returns_a_redirect_it_cannot_resolve():
    first_url = 'http://api.test/api/get'
    middleware = common._credentials_per_hop_middleware(first_url,
                                                        _jar(_scoped_cookies()))
    # Not a URL that urllib can join. The caller may have asked for the
    # redirect response itself, so it must come back as it is.
    assert await _headers_after_middleware(
        middleware, first_url, None,
        _FakeResponse(307, {'Location': 'http://[::1'})) == {
            'Cookie': 'api-cookie=for-api; host-cookie=for-host'
        }
    # The next request is handled on its own URL.
    assert await _headers_after_middleware(
        middleware, 'http://other.test/landing') == {
            'Cookie': 'other-cookie=for-other'
        }


@pytest.mark.asyncio
async def test_middleware_without_a_jar_leaves_the_cookie_header():
    first_url = 'http://api.test/api/get'
    middleware = common._credentials_per_hop_middleware(first_url, None)

    assert await _headers_after_middleware(middleware, first_url,
                                           {'Cookie': 'mine=1'}) == {
                                               'Cookie': 'mine=1'
                                           }


def test_cookie_lookup_orders_cookies_like_requests_does():
    url = 'http://api.test:46580/api/get'
    cookie_jar = _jar([
        _cookie('c-cookie', 'c', 'api.test'),
        _cookie('a-cookie', 'a', 'api.test'),
        _cookie('b-cookie', 'b', 'api.test'),
        _cookie('expired-cookie', 'expired', 'api.test', expires_in=-1000),
    ])

    sync_header = requests.Session().prepare_request(
        requests.Request('GET', url, cookies=cookie_jar)).headers.get('Cookie')
    assert sync_header in _in_any_order('a-cookie=a', 'b-cookie=b',
                                        'c-cookie=c')
    jar_as_sent = common._cookie_jar_of_sync_client_request(cookie_jar)
    assert common._cookie_header_of_sync_client(jar_as_sent, url) == sync_header
    # As in `requests`, the lookup runs on a copy: the jar of the caller is
    # left as it is, with its expired cookie.
    assert len(cookie_jar) == 4


def test_cookie_for_an_internationalized_host_matches_like_the_sync_client():
    # Stored for the encoded form of the host, as a server would set it.
    cookie_jar = _jar([_cookie('idn-cookie', 'for-idn', 'xn--bcher-kva.test')])
    url = 'https://bücher.test/api/get'

    sync_header = requests.Request(
        'GET', url, cookies=cookie_jar).prepare().headers.get('Cookie')
    assert sync_header == 'idn-cookie=for-idn'
    jar_as_sent = common._cookie_jar_of_sync_client_request(cookie_jar)
    assert common._cookie_header_of_sync_client(jar_as_sent, url) == sync_header
    # A redirect hop: the URL as aiohttp holds it.
    assert common._cookie_header_of_sync_client(jar_as_sent, str(
        yarl.URL(url))) == (sync_header)
