"""Containment of task file-mount sources to the caller's staging roots.

The API server translates a task's client-supplied ``file_mounts``/``workdir``
sources into server paths, and the backend then rsyncs those paths to the
caller's cluster. Without containment an authenticated caller can point a mount
at an arbitrary server file (``/etc/passwd``, another user's blobs, ...) and
have it exfiltrated. These tests cover the server-mode gate, the per-backend
roots, and the containment check in
``process_mounts_in_task_on_api_server``.
"""
import contextlib
import pathlib
import types
from unittest import mock

import pytest
import yaml

from sky.server import common
from sky.server.blob import blob_storage as bs
from sky.server.blob import local_blob_storage as lbs
from sky.skylet import constants

FLAG = constants.ENV_VAR_ENFORCE_MOUNT_CONTAINMENT
USER = 'alice'


# ---------------------------------------------------------------------------
# Server-mode gate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('deploy,host,expected', [
    (False, '127.0.0.1', False),
    (False, 'localhost', False),
    (False, '::1', False),
    (False, '0.0.0.0', True),
    (False, '10.0.0.5', True),
    (False, 'my-server.example.com', True),
    (True, '127.0.0.1', True),
    (True, '0.0.0.0', True),
])
def test_mount_containment_enforced(deploy, host, expected):
    # Deployed OR non-loopback bind enforces; a loopback `sky api start` is
    # exempt. An unclassifiable host is treated as reachable (enforce).
    assert common.mount_containment_enforced(deploy, host) is expected


def test_should_enforce_reads_flag(monkeypatch):
    monkeypatch.setenv(FLAG, '0')
    assert common.should_enforce_mount_containment() is False
    monkeypatch.setenv(FLAG, '1')
    assert common.should_enforce_mount_containment() is True


def test_should_enforce_unset_is_fail_closed(monkeypatch):
    # A missing flag must not silently disable the check.
    monkeypatch.delenv(FLAG, raising=False)
    assert common.should_enforce_mount_containment() is True


def test_set_mount_containment_enforced_roundtrip(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    common.set_mount_containment_enforced(True)
    assert common.should_enforce_mount_containment() is True
    common.set_mount_containment_enforced(False)
    assert common.should_enforce_mount_containment() is False


# ---------------------------------------------------------------------------
# Per-backend roots (abstract, no default)
# ---------------------------------------------------------------------------
def test_user_roots_is_abstract_no_default():
    # No base default: a backend that omits user_roots stays abstract and
    # cannot be instantiated, so the plugin cannot silently inherit the wrong
    # root and 400 every launch.
    assert 'user_roots' in bs.BlobStorage.__abstractmethods__


def test_local_backend_user_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(common, 'API_SERVER_CLIENT_DIR', tmp_path / 'clients')
    roots = lbs.LocalFilesystemBlobStorage().user_roots(USER)
    assert roots == [(tmp_path / 'clients' / USER / 'file_mounts').resolve()]
    # Blobs live under file_mounts, so the returned root contains them.
    blobs = lbs.LocalFilesystemBlobStorage().blobs_dir(USER)
    assert common._is_relative_to(blobs.resolve(), roots[0])


# ---------------------------------------------------------------------------
# process_mounts containment
# ---------------------------------------------------------------------------
@pytest.fixture
def local_server(monkeypatch, tmp_path):
    """A server whose blob backend stages under tmp_path/clients."""
    monkeypatch.setattr(common, 'API_SERVER_CLIENT_DIR', tmp_path / 'clients')
    monkeypatch.setattr(bs, '_blob_storage', lbs.LocalFilesystemBlobStorage())
    return tmp_path


def _run(cfg, *, enforce, workdir_only=False, blob_id=None):
    common.set_mount_containment_enforced(enforce)
    env = {constants.USER_ID_ENV_VAR: USER}
    return common.process_mounts_in_task_on_api_server(yaml.dump(cfg), env,
                                                       workdir_only, blob_id)


def test_reject_absolute_source_empty_mapping(local_server):
    # Empty mapping leaves the raw absolute source untouched -> exfil vector.
    with pytest.raises(ValueError, match='outside the allowed'):
        _run({'run': 'x', 'file_mounts': {'/dst': '/etc/passwd'}}, enforce=True)


def test_reject_absolute_workdir_empty_mapping(local_server):
    with pytest.raises(ValueError, match='outside the allowed'):
        _run({'run': 'x', 'workdir': '/etc'}, enforce=True)


def test_reject_dotdot_in_mapping_value(local_server):
    # A mapping value that escapes the staging base, even under enforcement-off
    # this is never legitimate, but we assert it under enforcement here.
    with pytest.raises(ValueError, match='outside the allowed'):
        _run(
            {
                'run': 'x',
                'file_mounts': {
                    '/dst': 'a'
                },
                'file_mounts_mapping': {
                    'a': '../../../../etc/passwd'
                },
            },
            enforce=True)


def test_reject_dotdot_in_mapping_value_even_when_exempt(local_server):
    # The `..`-in-mapping rejection is unconditional (a mapped value is server
    # staging, never `..`), so a loopback/exempt server still rejects it.
    with pytest.raises(ValueError, match='outside the allowed'):
        _run(
            {
                'run': 'x',
                'file_mounts': {
                    '/dst': 'a'
                },
                'file_mounts_mapping': {
                    'a': '../../../../etc/passwd'
                },
            },
            enforce=False)


def test_reject_source_under_shared_tmp(local_server, tmp_path):
    # ~/.sky/tmp-style shared staging is deliberately NOT a root (it would leak
    # other users' staged files).
    shared = tmp_path / 'shared_tmp' / 'secret'
    shared.parent.mkdir(parents=True)
    shared.write_text('x')
    with pytest.raises(ValueError, match='outside the allowed'):
        _run({'run': 'x', 'file_mounts': {'/dst': str(shared)}}, enforce=True)


def test_containment_not_bypassable_via_user_hash_traversal(local_server):
    # The containment roots are built from the caller's id; a traversal id like
    # '../..' would resolve a root to '/', letting /etc/passwd pass. The id is
    # validated (is_single_path_component) before roots are computed, so a
    # traversal id is rejected outright. This PR depends on that guard.
    common.set_mount_containment_enforced(True)
    env = {constants.USER_ID_ENV_VAR: '../../../../../..'}
    task = yaml.dump({'run': 'x', 'file_mounts': {'/dst': '/etc/passwd'}})
    with pytest.raises(ValueError):
        common.process_mounts_in_task_on_api_server(task, env, False, None)


def test_allow_mapped_source_under_clients(local_server):
    # Legit remote upload: mapping resolves under clients/<user>/file_mounts.
    _run(
        {
            'run': 'x',
            'file_mounts': {
                '/dst': 'proj'
            },
            'file_mounts_mapping': {
                'proj': 'proj'
            },
        },
        enforce=True)


def test_allow_source_under_blob_dir(local_server):
    # With a blob id, sources resolve under the blob dir (under file_mounts).
    blob_id = 'b' * 64
    blob_dir = lbs.LocalFilesystemBlobStorage().blobs_dir(USER) / blob_id
    blob_dir.mkdir(parents=True)
    _run(
        {
            'run': 'x',
            'file_mounts': {
                '/dst': 'f'
            },
            'file_mounts_mapping': {
                'f': 'f'
            },
        },
        enforce=True,
        blob_id=blob_id)


def test_allow_cloud_store_source(local_server):
    # Cloud sources are never staged on the server; skipped by containment.
    _run({'run': 'x', 'file_mounts': {'/dst': 's3://bucket/key'}}, enforce=True)


def test_reject_dict_source_absolute(local_server):
    # file_mounts value can be a storage dict {source: ...}; the source is
    # contained too.
    with pytest.raises(ValueError, match='outside the allowed'):
        _run({
            'run': 'x',
            'file_mounts': {
                '/dst': {
                    'source': '/etc/passwd'
                }
            }
        },
             enforce=True)


def test_reject_list_source_absolute(local_server):
    # A storage dict source can be a list; every element is contained.
    with pytest.raises(ValueError, match='outside the allowed'):
        _run({
            'run': 'x',
            'file_mounts': {
                '/dst': {
                    'source': ['/etc/passwd']
                }
            }
        },
             enforce=True)


def test_reject_service_tls_absolute(local_server):
    # service.tls keyfile/certfile are translated sources and are contained.
    with pytest.raises(ValueError, match='outside the allowed'):
        _run(
            {
                'run': 'x',
                'service': {
                    'tls': {
                        'keyfile': '/etc/passwd',
                        'certfile': '/etc/passwd'
                    }
                }
            },
            enforce=True)


def test_exempt_server_allows_absolute_local_source(local_server):
    # Baseline arm: a loopback local server legitimately mounts local paths.
    _run({'run': 'x', 'file_mounts': {'/dst': '/etc/hosts'}}, enforce=False)


def test_ha_backend_roots_are_honored(local_server, tmp_path):
    # A shared-FS backend resolves blobs outside clients/<user>; the check must
    # use the backend's own roots or it would 400 every HA launch.
    shared_blobs = tmp_path / 'skypilot' / 'shared' / 'blobs' / USER
    local_cache = tmp_path / 'blob-cache' / USER
    for d in (shared_blobs, local_cache):
        d.mkdir(parents=True)

    class _HABackend(lbs.LocalFilesystemBlobStorage):

        def user_roots(self, user_id):
            return [shared_blobs, local_cache]

    with mock.patch.object(bs, '_blob_storage', _HABackend()):
        # A source under the shared tree is allowed.
        src = shared_blobs / 'x'
        src.write_text('x')
        _run({'run': 'x', 'file_mounts': {'/dst': str(src)}}, enforce=True)
        # A source under the local cache is allowed.
        src2 = local_cache / 'y'
        src2.write_text('y')
        _run({'run': 'x', 'file_mounts': {'/dst': str(src2)}}, enforce=True)
        # Anything else is still rejected.
        with pytest.raises(ValueError, match='outside the allowed'):
            _run({
                'run': 'x',
                'file_mounts': {
                    '/dst': '/etc/passwd'
                }
            },
                 enforce=True)


# ---------------------------------------------------------------------------
# Forge protection: a client cannot disable enforcement via request env vars.
# ---------------------------------------------------------------------------
def test_flag_stripped_from_client_env_vars(monkeypatch):
    # The executor overlays request env vars into os.environ before running
    # process_mounts, so any SKYPILOT_SERVER_-prefixed key (including the
    # containment flag) must be stripped from the client's env vars first, or a
    # crafted request could set the flag to '0' and disable the check.
    import os

    from sky.server.requests import executor

    monkeypatch.setenv(FLAG, '1')  # server says: enforce
    fake_server_var = constants.SKYPILOT_SERVER_ENV_VAR_PREFIX + 'FAKE'

    request_body = types.SimpleNamespace(
        env_vars={
            FLAG: '0',  # attacker tries to disable enforcement
            fake_server_var: 'x',  # any server-prefixed key is stripped too
            constants.USER_ID_ENV_VAR: USER,
            constants.USER_ENV_VAR: USER,
        },
        entrypoint='e',
        entrypoint_command='c',
        override_skypilot_config={},
        override_skypilot_config_path=None,
        using_remote_api_server=False,
        client_api_version=None,
        workspace_access=None,
    )

    user = types.SimpleNamespace(id=USER, name=USER)
    monkeypatch.setattr(executor.global_user_state,
                        'add_or_update_user',
                        lambda u, return_user=False: (None, user))
    monkeypatch.setattr(executor.server_common, 'reload_for_new_request',
                        lambda **kw: None)
    monkeypatch.setattr(executor.skypilot_config, 'override_skypilot_config',
                        lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(executor, '_should_apply_workspace_resolver',
                        lambda *a, **k: False)
    monkeypatch.setattr(executor.workspaces_core,
                        'reject_request_for_unauthorized_workspace',
                        lambda *a, **k: None)

    with executor.override_request_env_and_config(request_body, 'req-1',
                                                  'sky.launch'):
        assert common.should_enforce_mount_containment() is True
        # The client-supplied server-prefixed keys never reached os.environ.
        assert os.environ.get(fake_server_var) is None


def test_init_respects_explicit_flag(monkeypatch):
    # An operator's explicit value wins over the computed default, so a deployed
    # server can be told to skip enforcement (or forced to enforce).
    monkeypatch.setenv(FLAG, '0')
    common.init_mount_containment_enforced(deploy=True, host='0.0.0.0')
    assert common.should_enforce_mount_containment() is False
    monkeypatch.setenv(FLAG, '1')
    common.init_mount_containment_enforced(deploy=False, host='127.0.0.1')
    assert common.should_enforce_mount_containment() is True


def test_init_computes_default_when_unset(monkeypatch):
    # Unset -> compute from server mode: deployed enforces, loopback exempt.
    monkeypatch.delenv(FLAG, raising=False)
    common.init_mount_containment_enforced(deploy=True, host='127.0.0.1')
    assert common.should_enforce_mount_containment() is True
    monkeypatch.delenv(FLAG, raising=False)
    common.init_mount_containment_enforced(deploy=False, host='127.0.0.1')
    assert common.should_enforce_mount_containment() is False
