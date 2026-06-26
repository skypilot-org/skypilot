"""Tests for the GC pause provider seam.

The feature is a gate (``skypilot_config.gc_should_skip``) plus a provider seam
(``sky.server.gc``) with a no-op default. These tests cover:

* the default provider reports "not paused" so GC loops run;
* the registration hook installs/overrides the active provider and resets
  cleanly between tests;
* the gate consults the active provider and logs/returns True when paused;
* each gated server-side GC loop consults the gate and skips its destructive
  body when paused (with a stub provider that reports paused).
"""
import asyncio
import datetime
from typing import Optional
from unittest import mock

import pytest

from sky import global_user_state
from sky import skypilot_config
from sky.jobs import state as jobs_state
from sky.server import daemons
from sky.server import gc as gc_provider
from sky.server import server as api_server
from sky.server.requests import requests as api_requests


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


@pytest.fixture(autouse=True)
def _reset_provider():
    """Reset to the no-op default before and after every test."""
    gc_provider.reset_gc_pause_provider()
    yield
    gc_provider.reset_gc_pause_provider()


class _PausedProvider(gc_provider.GCPauseProvider):
    """Stub provider that always reports paused."""

    def __init__(self, until: Optional[datetime.datetime] = None):
        self._until = until

    def is_paused(self) -> bool:
        return True

    def paused_until(self):
        return self._until


# ---------------------------------------------------------------------------
# Provider seam: default no-op + registration/override/reset.
# ---------------------------------------------------------------------------


def test_default_provider_is_noop_and_not_paused():
    provider = gc_provider.get_gc_pause_provider()
    assert isinstance(provider, gc_provider.NoOpGCPauseProvider)
    assert provider.is_paused() is False
    assert provider.paused_until() is None


def test_register_overrides_active_provider():
    paused = _PausedProvider()
    gc_provider.set_gc_pause_provider(paused)
    assert gc_provider.get_gc_pause_provider() is paused
    assert gc_provider.get_gc_pause_provider().is_paused() is True


def test_register_alias_matches_set():
    # The register_* verb alias is the same hook as set_*.
    assert (gc_provider.register_gc_pause_provider is
            gc_provider.set_gc_pause_provider)


def test_reset_restores_noop_default():
    gc_provider.set_gc_pause_provider(_PausedProvider())
    gc_provider.reset_gc_pause_provider()
    assert isinstance(gc_provider.get_gc_pause_provider(),
                      gc_provider.NoOpGCPauseProvider)
    assert gc_provider.get_gc_pause_provider().is_paused() is False


# ---------------------------------------------------------------------------
# gc_should_skip(): reloads config and consults the active provider.
# ---------------------------------------------------------------------------


def test_gc_should_skip_false_with_default_provider():
    with mock.patch.object(skypilot_config, 'reload_config') as mock_reload:
        assert skypilot_config.gc_should_skip('loop') is False
        mock_reload.assert_called_once()


def test_gc_should_skip_true_when_provider_paused():
    until = _now() + datetime.timedelta(hours=1)
    gc_provider.set_gc_pause_provider(_PausedProvider(until=until))
    with mock.patch.object(skypilot_config, 'reload_config') as mock_reload:
        assert skypilot_config.gc_should_skip('loop') is True
        mock_reload.assert_called_once()


# ---------------------------------------------------------------------------
# Each gated loop consults the gate and skips its destructive body when paused.
# ---------------------------------------------------------------------------


class _StopLoop(Exception):
    """Raised from a patched sleep to break a daemon's `while True`."""


@pytest.mark.asyncio
async def test_cleanup_unreferenced_file_mounts_skips_when_paused():
    with mock.patch.object(skypilot_config, 'gc_should_skip',
                           return_value=True), \
         mock.patch.object(asyncio, 'to_thread') as mock_to_thread, \
         mock.patch.object(api_server.asyncio, 'sleep',
                           side_effect=[None, _StopLoop()]):
        with pytest.raises(_StopLoop):
            await api_server.cleanup_unreferenced_file_mounts()
        # The destructive cleanup (run via to_thread) must not be invoked.
        mock_to_thread.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_download_tmp_skips_when_paused():
    with mock.patch.object(skypilot_config, 'gc_should_skip',
                           return_value=True), \
         mock.patch.object(api_server.bs, 'get_blob_storage') as mock_blob, \
         mock.patch.object(api_server.asyncio, 'sleep',
                           side_effect=[None, _StopLoop()]):
        with pytest.raises(_StopLoop):
            await api_server.cleanup_download_tmp()
        # No blob storage access => no destructive scan.
        mock_blob.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_upload_ids_skips_when_paused():
    with mock.patch.object(skypilot_config, 'gc_should_skip',
                           return_value=True), \
         mock.patch.dict(api_server.upload_ids_to_cleanup,
                         {('uid', 'user'): _now()}, clear=True), \
         mock.patch.object(api_server.shutil, 'rmtree') as mock_rmtree, \
         mock.patch.object(api_server.asyncio, 'sleep',
                           side_effect=[None, _StopLoop()]):
        with pytest.raises(_StopLoop):
            await api_server.cleanup_upload_ids()
        # Paused => the pending upload id is not deleted.
        mock_rmtree.assert_not_called()
        assert ('uid', 'user') in api_server.upload_ids_to_cleanup


@pytest.mark.asyncio
async def test_requests_gc_daemon_skips_when_paused():
    with mock.patch.object(skypilot_config, 'gc_should_skip',
                           return_value=True), \
         mock.patch.object(api_requests,
                           'clean_finished_requests_with_retention',
                           new_callable=mock.AsyncMock) as mock_clean, \
         mock.patch.object(api_requests.asyncio, 'sleep',
                           side_effect=[_StopLoop()]):
        with pytest.raises(_StopLoop):
            await api_requests.requests_gc_daemon()
        mock_clean.assert_not_called()


@pytest.mark.asyncio
async def test_cluster_event_retention_daemon_skips_when_paused():
    with mock.patch.object(skypilot_config, 'gc_should_skip',
                           return_value=True), \
         mock.patch.object(global_user_state,
                           'cleanup_cluster_events_with_retention'
                          ) as mock_cleanup, \
         mock.patch.object(global_user_state.asyncio, 'sleep',
                           side_effect=[_StopLoop()]):
        with pytest.raises(_StopLoop):
            await global_user_state.cluster_event_retention_daemon()
        mock_cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_job_event_retention_daemon_skips_when_paused():
    with mock.patch.object(skypilot_config, 'gc_should_skip',
                           return_value=True), \
         mock.patch.object(jobs_state,
                           'cleanup_job_events_with_retention_async',
                           new_callable=mock.AsyncMock) as mock_cleanup, \
         mock.patch.object(jobs_state.asyncio, 'sleep',
                           side_effect=[_StopLoop()]):
        with pytest.raises(_StopLoop):
            await jobs_state.job_event_retention_daemon()
        mock_cleanup.assert_not_called()


def test_expired_token_cleanup_event_skips_when_paused():
    with mock.patch.object(skypilot_config, 'gc_should_skip',
                           return_value=True), \
         mock.patch.object(skypilot_config, 'get_nested', return_value=0), \
         mock.patch('sky.jobs.utils.cleanup_expired_api_access_tokens'
                   ) as mock_cleanup, \
         mock.patch.object(daemons.time, 'sleep'):
        daemons.expired_token_cleanup_event()
        mock_cleanup.assert_not_called()
