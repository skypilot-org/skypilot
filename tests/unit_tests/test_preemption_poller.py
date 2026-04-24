"""Unit tests for `sky.skylet.preemption_poller`.

These tests exercise the per-cloud metadata pollers with mocked HTTP
responses — no real cloud calls. The poller's only externally visible
effect on detection is sending SIGTERM to the skylet process; the
tests assert on that.
"""

import os
import signal
import threading
import time
from unittest import mock
from urllib import error as urlerr

import pytest

from sky.skylet import preemption_poller


@pytest.fixture(autouse=True)
def _fast_intervals(monkeypatch):
    """Make polling tight so tests finish quickly."""
    monkeypatch.setattr(preemption_poller, '_AWS_POLL_INTERVAL_SECONDS', 0.01)
    monkeypatch.setattr(preemption_poller, '_GCP_POLL_INTERVAL_SECONDS', 0.01)
    monkeypatch.setattr(preemption_poller, '_AZURE_POLL_INTERVAL_SECONDS', 0.01)
    yield


@pytest.fixture
def captured_sigterm(monkeypatch):
    """Capture SIGTERM without actually killing the test process.

    Returns an Event that is set on first SIGTERM delivery.
    """
    fired = threading.Event()

    def fake_kill(pid, sig):
        assert sig == signal.SIGTERM
        assert pid == os.getpid()
        fired.set()

    monkeypatch.setattr(preemption_poller.os, 'kill', fake_kill)
    return fired


# ---- AWS (IMDSv2) -------------------------------------------------------


def _fake_aws_urlopen(spot_action: str = ''):
    """Returns a fake urlopen callable for AWS IMDSv2.

    ``spot_action`` is the body returned from the spot/instance-action
    endpoint — the empty string simulates "no action scheduled".
    """

    class FakeResponse:

        def __init__(self, body: bytes, status: int):
            self._body = body
            self.status = status

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout=None):  # pylint: disable=unused-argument
        url = request.full_url if hasattr(request, 'full_url') else request
        if 'api/token' in url:
            return FakeResponse(b'token-abc', 200)
        if 'spot/instance-action' in url:
            if spot_action:
                return FakeResponse(spot_action.encode(), 200)
            # AWS returns 404 when no action scheduled.
            raise urlerr.HTTPError(url, 404, 'Not Found', {}, None)
        raise AssertionError(f'Unexpected IMDS URL: {url}')

    return urlopen


def test_aws_poller_no_action_no_sigterm(captured_sigterm, monkeypatch):
    monkeypatch.setattr(preemption_poller.urllib.request, 'urlopen',
                        _fake_aws_urlopen(''))
    stop = threading.Event()
    t = threading.Thread(target=preemption_poller._poll_aws,
                         args=(stop,),
                         daemon=True)
    t.start()
    # Let it loop a few times then stop.
    time.sleep(0.1)
    stop.set()
    t.join(timeout=1)
    assert not captured_sigterm.is_set()


def test_aws_poller_fires_on_instance_action(captured_sigterm, monkeypatch):
    body = '{"action":"terminate","time":"2026-01-01T00:00:00Z"}'
    monkeypatch.setattr(preemption_poller.urllib.request, 'urlopen',
                        _fake_aws_urlopen(body))
    stop = threading.Event()
    t = threading.Thread(target=preemption_poller._poll_aws,
                         args=(stop,),
                         daemon=True)
    t.start()
    assert captured_sigterm.wait(timeout=2)
    stop.set()
    t.join(timeout=1)


# ---- GCP (metadata wait_for_change) -------------------------------------


def _fake_gcp_urlopen(preempted: str):

    class FakeResponse:

        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout=None):  # pylint: disable=unused-argument
        url = request.full_url if hasattr(request, 'full_url') else request
        # Both preempted and maintenance-event endpoints covered here.
        return FakeResponse(preempted.encode())

    return urlopen


def test_gcp_poller_no_preempt(captured_sigterm, monkeypatch):
    monkeypatch.setattr(preemption_poller.urllib.request, 'urlopen',
                        _fake_gcp_urlopen('FALSE'))
    stop = threading.Event()
    t = threading.Thread(target=preemption_poller._poll_gcp,
                         args=(stop,),
                         daemon=True)
    t.start()
    time.sleep(0.1)
    stop.set()
    t.join(timeout=1)
    assert not captured_sigterm.is_set()


def test_gcp_poller_fires_on_preempt(captured_sigterm, monkeypatch):
    monkeypatch.setattr(preemption_poller.urllib.request, 'urlopen',
                        _fake_gcp_urlopen('TRUE'))
    stop = threading.Event()
    t = threading.Thread(target=preemption_poller._poll_gcp,
                         args=(stop,),
                         daemon=True)
    t.start()
    assert captured_sigterm.wait(timeout=2)
    stop.set()
    t.join(timeout=1)


# ---- Azure (scheduled events) -------------------------------------------


def _fake_azure_urlopen(events_json: str):

    class FakeResponse:

        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout=None):  # pylint: disable=unused-argument
        return FakeResponse(events_json.encode())

    return urlopen


def test_azure_poller_no_events(captured_sigterm, monkeypatch):
    monkeypatch.setattr(
        preemption_poller.urllib.request, 'urlopen',
        _fake_azure_urlopen('{"DocumentIncarnation":1,'
                            '"Events":[]}'))
    stop = threading.Event()
    t = threading.Thread(target=preemption_poller._poll_azure,
                         args=(stop,),
                         daemon=True)
    t.start()
    time.sleep(0.1)
    stop.set()
    t.join(timeout=1)
    assert not captured_sigterm.is_set()


def test_azure_poller_fires_on_preempt(captured_sigterm, monkeypatch):
    events = ('{"DocumentIncarnation":2,'
              '"Events":[{"EventType":"Preempt","Resources":["vm-1"]}]}')
    monkeypatch.setattr(preemption_poller.urllib.request, 'urlopen',
                        _fake_azure_urlopen(events))
    stop = threading.Event()
    t = threading.Thread(target=preemption_poller._poll_azure,
                         args=(stop,),
                         daemon=True)
    t.start()
    assert captured_sigterm.wait(timeout=2)
    stop.set()
    t.join(timeout=1)


def test_azure_poller_ignores_non_preempt_events(captured_sigterm, monkeypatch):
    # Freeze/Reboot/Redeploy must not trigger the preemption hook.
    events = ('{"DocumentIncarnation":3,'
              '"Events":[{"EventType":"Reboot","Resources":["vm-1"]}]}')
    monkeypatch.setattr(preemption_poller.urllib.request, 'urlopen',
                        _fake_azure_urlopen(events))
    stop = threading.Event()
    t = threading.Thread(target=preemption_poller._poll_azure,
                         args=(stop,),
                         daemon=True)
    t.start()
    time.sleep(0.1)
    stop.set()
    t.join(timeout=1)
    assert not captured_sigterm.is_set()


# ---- start() dispatch ---------------------------------------------------


@pytest.mark.parametrize('cloud,loop', [('aws', '_poll_aws'),
                                        ('gcp', '_poll_gcp'),
                                        ('azure', '_poll_azure')])
def test_start_dispatches_correct_cloud(cloud, loop):
    with mock.patch.object(preemption_poller, loop) as m:
        stop = preemption_poller.start(cloud)
        # Poller is daemonized; give the thread a moment to call into the
        # patched loop.
        time.sleep(0.05)
        m.assert_called_once()
        stop.set()


def test_start_unknown_cloud_raises():
    with pytest.raises(ValueError):
        preemption_poller.start('oracle')
