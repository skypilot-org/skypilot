"""The async network probe must read the proxy environment, like its twin.

`requests.Session` trusts the environment by default and aiohttp does not, so
the two implementations of this check disagreed wherever outbound HTTPS goes
through a proxy: the sync one passed and the async one failed forever. The
managed job controller gates every job-status poll on the async one, so a job
whose task had already succeeded stayed RUNNING indefinitely.
"""
import asyncio
from unittest import mock

import aiohttp
import pytest

from sky import exceptions
from sky.backends import backend_utils


class _Response:

    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Session:
    """Records how it was constructed and what it was asked for."""

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        _Session.last = self
        self.requested = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def head(self, url):
        self.requested.append(url)
        return _Session.responder(url)


def _run(monkeypatch, responder):
    _Session.responder = staticmethod(responder)
    monkeypatch.setattr(aiohttp, 'ClientSession', _Session)
    return asyncio.run(backend_utils.async_check_network_connection())


def test_the_session_trusts_the_proxy_environment(monkeypatch):
    """The fix. Without `trust_env`, aiohttp ignores `https_proxy` and
    connects directly -- measured as a 31s timeout on a host whose direct
    egress is blocked, against 0.64s through the proxy."""
    _run(monkeypatch, lambda url: _Response(302))
    assert _Session.last.kwargs.get('trust_env') is True


def test_a_reachable_address_short_circuits(monkeypatch):
    _run(monkeypatch, lambda url: _Response(200))
    assert len(_Session.last.requested) == 1


def test_the_second_address_is_tried_when_the_first_fails(monkeypatch):

    def responder(url):
        if url == backend_utils._TEST_IP_LIST[0]:
            raise aiohttp.ClientError('refused')
        return _Response(302)

    _run(monkeypatch, responder)
    assert _Session.last.requested == list(backend_utils._TEST_IP_LIST)


def test_every_address_failing_raises(monkeypatch):

    def responder(url):
        raise aiohttp.ClientError('refused')

    with pytest.raises(exceptions.NetworkError):
        _run(monkeypatch, responder)


def test_an_error_status_from_the_last_address_is_not_success(monkeypatch):
    """A bad status is not an exception, so the previous form -- which only
    raised when the *last* address threw -- returned success after being
    refused by it. Gating a job-status poll on that is the false alarm this
    check exists to prevent."""
    with pytest.raises(exceptions.NetworkError):
        _run(monkeypatch, lambda url: _Response(403))
