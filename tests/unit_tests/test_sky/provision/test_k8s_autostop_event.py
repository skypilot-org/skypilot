"""Tests for the Kubernetes autodown event breadcrumb reader."""
import datetime
from typing import Optional
from unittest import mock

from sky import exceptions
from sky.provision.kubernetes import instance as k8s_instance
from sky.provision.kubernetes import reads

_PROVIDER_CONFIG = {'namespace': 'sky-ns', 'context': 'my-ctx'}


def _make_event(name: str,
                reason: str = k8s_instance.AUTOSTOP_EVENT_REASON,
                message: str = 'autodowning',
                last_timestamp: Optional[datetime.datetime] = None):
    """Build a mock core/v1 Event with the fields the reader inspects."""
    event = mock.MagicMock()
    event.reason = reason
    event.message = message
    event.involved_object.name = name
    event.last_timestamp = last_timestamp
    event.event_time = None
    event.metadata.creation_timestamp = last_timestamp
    return event


def _patch_reads(monkeypatch, events=None, raises=None):
    """Fakes the read of the namespace's autodown events."""
    list_objects = mock.MagicMock()
    if raises is not None:
        list_objects.side_effect = raises
    else:
        list_objects.return_value = reads.ReadResult(items=events or [],
                                                     resource_version='',
                                                     staleness_seconds=0,
                                                     reason=None)
    monkeypatch.setattr(reads, 'list_objects', list_objects)
    return list_objects


def test_autostop_event_matches_head_pod(monkeypatch):
    ts = datetime.datetime(2026, 6, 5, tzinfo=datetime.timezone.utc)
    _patch_reads(monkeypatch,
                 events=[_make_event('my-cluster-abc-head', last_timestamp=ts)])

    result = k8s_instance.get_cluster_autostop_event(_PROVIDER_CONFIG,
                                                     'my-cluster-abc')

    assert result is not None
    assert result['reason'] == k8s_instance.AUTOSTOP_EVENT_REASON
    assert result['transitioned_at'] == int(ts.timestamp())


def test_autostop_event_ignores_other_clusters(monkeypatch):
    ts = datetime.datetime(2026, 6, 5, tzinfo=datetime.timezone.utc)
    _patch_reads(monkeypatch,
                 events=[_make_event('other-cluster-head', last_timestamp=ts)])

    result = k8s_instance.get_cluster_autostop_event(_PROVIDER_CONFIG,
                                                     'my-cluster-abc')

    assert result is None


def test_autostop_event_ignores_prefix_sibling(monkeypatch):
    # A sibling cluster whose name shares this cluster's prefix must not match;
    # the head pod name is matched exactly, not by prefix.
    ts = datetime.datetime(2026, 6, 5, tzinfo=datetime.timezone.utc)
    _patch_reads(
        monkeypatch,
        events=[_make_event('my-cluster-abc-2-head', last_timestamp=ts)])

    result = k8s_instance.get_cluster_autostop_event(_PROVIDER_CONFIG,
                                                     'my-cluster-abc')

    assert result is None


def test_autostop_event_ignores_stale_before_since(monkeypatch):
    # A breadcrumb from a previous incarnation (before the current launch) must
    # be ignored so a relaunched-then-torn-down cluster is not misattributed.
    stale = datetime.datetime(2026, 6, 5, 10, tzinfo=datetime.timezone.utc)
    _patch_reads(
        monkeypatch,
        events=[_make_event('my-cluster-abc-head', last_timestamp=stale)])
    launched_at = datetime.datetime(2026,
                                    6,
                                    5,
                                    11,
                                    tzinfo=datetime.timezone.utc).timestamp()

    result = k8s_instance.get_cluster_autostop_event(_PROVIDER_CONFIG,
                                                     'my-cluster-abc',
                                                     since=launched_at)

    assert result is None


def test_autostop_event_kept_after_since(monkeypatch):
    fresh = datetime.datetime(2026, 6, 5, 12, tzinfo=datetime.timezone.utc)
    _patch_reads(
        monkeypatch,
        events=[_make_event('my-cluster-abc-head', last_timestamp=fresh)])
    launched_at = datetime.datetime(2026,
                                    6,
                                    5,
                                    11,
                                    tzinfo=datetime.timezone.utc).timestamp()

    result = k8s_instance.get_cluster_autostop_event(_PROVIDER_CONFIG,
                                                     'my-cluster-abc',
                                                     since=launched_at)

    assert result is not None
    assert result['transitioned_at'] == int(fresh.timestamp())


def test_autostop_event_reads_the_namespace_events(monkeypatch):
    list_objects = _patch_reads(monkeypatch, events=[])

    k8s_instance.get_cluster_autostop_event(_PROVIDER_CONFIG, 'my-cluster-abc')

    assert list_objects.call_args.args == ('my-ctx', reads.EVENTS)
    kwargs = list_objects.call_args.kwargs
    assert kwargs['namespace'] == 'sky-ns'
    assert kwargs['field_selector'] == (
        f'reason={k8s_instance.AUTOSTOP_EVENT_REASON}')
    assert kwargs['consistency'] == reads.CACHED


def test_autostop_event_returns_latest(monkeypatch):
    older = datetime.datetime(2026, 6, 5, 10, tzinfo=datetime.timezone.utc)
    newer = datetime.datetime(2026, 6, 5, 12, tzinfo=datetime.timezone.utc)
    _patch_reads(monkeypatch,
                 events=[
                     _make_event('my-cluster-abc-head',
                                 message='old',
                                 last_timestamp=older),
                     _make_event('my-cluster-abc-head',
                                 message='new',
                                 last_timestamp=newer),
                 ])

    result = k8s_instance.get_cluster_autostop_event(_PROVIDER_CONFIG,
                                                     'my-cluster-abc')

    assert result is not None
    assert result['message'] == 'new'
    assert result['transitioned_at'] == int(newer.timestamp())


def test_autostop_event_no_events(monkeypatch):
    _patch_reads(monkeypatch, events=[])

    assert k8s_instance.get_cluster_autostop_event(_PROVIDER_CONFIG,
                                                   'my-cluster-abc') is None


def test_autostop_event_never_raises(monkeypatch):
    _patch_reads(monkeypatch,
                 raises=exceptions.KubeAPIUnreachableError('k8s API down'))

    # Best-effort diagnostics: an API error resolves to None, not an exception.
    assert k8s_instance.get_cluster_autostop_event(_PROVIDER_CONFIG,
                                                   'my-cluster-abc') is None
