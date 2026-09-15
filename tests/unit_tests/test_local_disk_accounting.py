"""Tests for the API server's local-disk accounting and its metrics.

The collector is exercised unwrapped: ResilientCollector refreshes on a
background thread, so a test that went through it would assert against
whichever snapshot happened to be current.
"""
import errno
import os

import pytest

from sky.server import local_disk
from sky.server import metrics


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'x' * size)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Two real roots plus one that does not exist."""
    present = tmp_path / 'present'
    other = tmp_path / 'other'
    present.mkdir()
    other.mkdir()
    mapping = {
        'present': str(present),
        'other': str(other),
        'absent': str(tmp_path / 'absent'),
    }
    monkeypatch.setattr(local_disk, 'local_roots', lambda: mapping)
    return present, other


@pytest.fixture(autouse=True)
def _no_budget(monkeypatch):
    monkeypatch.delenv(local_disk.EPHEMERAL_STORAGE_LIMIT_ENV_VAR,
                       raising=False)
    monkeypatch.delenv(local_disk.EPHEMERAL_STORAGE_REQUEST_ENV_VAR,
                       raising=False)


def test_scan_counts_files_and_skips_absent_roots(roots):
    present, _ = roots
    _write(str(present / 'a.log'), 128 * 1024)
    _write(str(present / 'nested' / 'b.log'), 64 * 1024)

    snapshot = local_disk.scan()

    # An absent root emits nothing rather than a measured zero.
    assert set(snapshot.roots) == {'present', 'other'}
    assert snapshot.roots['present'].files == 2
    assert snapshot.roots['other'].files == 0
    # Allocated blocks, so at least the bytes written plus directory inodes.
    assert snapshot.roots['present'].used_bytes >= 192 * 1024
    assert not snapshot.roots['present'].truncated
    assert snapshot.used_bytes == sum(
        r.used_bytes for r in snapshot.roots.values())


def test_scan_counts_a_hard_link_once(roots):
    present, _ = roots
    target = str(present / 'a.log')
    _write(target, 1024 * 1024)
    before = local_disk.scan().roots['present']

    os.link(target, str(present / 'link.log'))
    after = local_disk.scan().roots['present']

    assert after.files == before.files
    # The extra dirent may enlarge the directory inode, but the file's
    # megabyte must not be counted twice.
    assert after.used_bytes - before.used_bytes < 1024 * 1024


def test_scan_reports_truncation_instead_of_undercounting_silently(roots):
    present, _ = roots
    for i in range(20):
        _write(str(present / f'{i}.log'), 1024)

    snapshot = local_disk.scan(max_entries=5)

    assert snapshot.roots['present'].truncated is True
    assert snapshot.roots['present'].files < 20


def test_scan_reports_the_filesystem_behind_each_root(roots):
    present, _ = roots
    _write(str(present / 'a.log'), 1024)

    snapshot = local_disk.scan()

    # Both roots are under tmp_path, so they share one mount point.
    assert len(snapshot.filesystems) == 1
    fs = next(iter(snapshot.filesystems.values()))
    assert fs.size_bytes > 0
    assert fs.avail_bytes > 0
    assert fs.avail_bytes <= fs.size_bytes


def test_budget_prefers_the_limit_over_the_request(monkeypatch):
    monkeypatch.setenv(local_disk.EPHEMERAL_STORAGE_REQUEST_ENV_VAR, '100')
    request = local_disk.budget()
    assert request.total_bytes == 100
    assert request.source == local_disk.BUDGET_SOURCE_REQUEST
    assert request.enforced is False

    monkeypatch.setenv(local_disk.EPHEMERAL_STORAGE_LIMIT_ENV_VAR, '250')
    limit = local_disk.budget()
    assert limit.total_bytes == 250
    assert limit.source == local_disk.BUDGET_SOURCE_LIMIT
    assert limit.enforced is True


@pytest.mark.parametrize('raw', ['', 'not-a-number', '0', '-1'])
def test_budget_absent_when_unusable(monkeypatch, raw):
    monkeypatch.setenv(local_disk.EPHEMERAL_STORAGE_LIMIT_ENV_VAR, raw)
    assert local_disk.budget() is None


def test_a_request_is_not_headroom(roots, monkeypatch):
    """A request does not stop the container, so it cannot bound headroom.

    Reporting room left against a scheduling request would reach zero
    while the disk still has space, and read as imminent death.
    """
    present, _ = roots
    _write(str(present / 'a.log'), 64 * 1024)
    monkeypatch.setenv(local_disk.EPHEMERAL_STORAGE_REQUEST_ENV_VAR, '4096')

    snapshot = local_disk.scan()

    assert snapshot.budget.source == local_disk.BUDGET_SOURCE_REQUEST
    # Already over the request, and still no headroom claim either way.
    assert snapshot.used_bytes > 4096
    assert snapshot.headroom_bytes is None


def test_headroom_needs_a_limit_and_never_goes_negative(roots, monkeypatch):
    present, _ = roots
    _write(str(present / 'a.log'), 64 * 1024)

    assert local_disk.scan().headroom_bytes is None

    monkeypatch.setenv(local_disk.EPHEMERAL_STORAGE_LIMIT_ENV_VAR, str(1024**3))
    snapshot = local_disk.scan()
    assert snapshot.headroom_bytes == 1024**3 - snapshot.used_bytes

    monkeypatch.setenv(local_disk.EPHEMERAL_STORAGE_LIMIT_ENV_VAR, '1')
    assert local_disk.scan().headroom_bytes == 0


def _families(collector):
    return {m.name: m for m in collector.collect()}


def test_collector_omits_budget_series_when_the_container_has_no_budget(roots):
    present, _ = roots
    _write(str(present / 'a.log'), 4096)

    families = _families(metrics.LocalDiskUsageCollector())

    assert families['sky_apiserver_local_disk_budget_bytes'].samples == []
    assert families['sky_apiserver_local_disk_headroom_bytes'].samples == []
    unreadable = families[
        'sky_apiserver_local_disk_scan_unreadable_entries'].samples
    assert all(s.value == 0 for s in unreadable)
    used = {
        s.labels['root']: s.value
        for s in families['sky_apiserver_local_disk_used_bytes'].samples
    }
    assert set(used) == {'present', 'other'}
    assert used['present'] >= 4096
    truncated = families['sky_apiserver_local_disk_scan_truncated'].samples
    assert all(s.value == 0 for s in truncated)
    duration, = families[
        'sky_apiserver_local_disk_scan_duration_seconds'].samples
    assert duration.value >= 0


def test_collector_emits_budget_and_headroom_when_exposed(roots, monkeypatch):
    present, _ = roots
    _write(str(present / 'a.log'), 4096)
    monkeypatch.setenv(local_disk.EPHEMERAL_STORAGE_LIMIT_ENV_VAR,
                       str(8 * 1024**3))

    families = _families(metrics.LocalDiskUsageCollector())

    budget, = families['sky_apiserver_local_disk_budget_bytes'].samples
    headroom, = families['sky_apiserver_local_disk_headroom_bytes'].samples
    assert budget.value == 8 * 1024**3
    assert budget.labels == {'source': local_disk.BUDGET_SOURCE_LIMIT}
    assert 0 < headroom.value <= budget.value


def test_local_roots_includes_the_blob_backend_and_not_shared_sky_logs():
    roots = local_disk.local_roots()

    assert 'request_logs' in roots
    assert 'request_debug_logs' in roots
    # Provided by BlobStorage.local_disk_roots(); the default backend keeps
    # extracted file mounts under the clients dir.
    assert 'api_server_clients' in roots
    assert not any('sky_logs' in name for name in roots)


def test_only_charged_roots_count_against_the_budget(roots, monkeypatch):
    """A persistent volume mounted into the tree is not this pod's problem.

    The chart mounts a PVC at ~/.sky/api_server/clients under one upgrade
    strategy and over all of ~/.sky under another, so the same root is
    local in one layout and shared in the next. Counting the shared bytes
    would subtract another volume's usage from this container's budget and
    report exhaustion that is not there.
    """
    present, other = roots
    _write(str(present / 'a.log'), 256 * 1024)
    _write(str(other / 'b.log'), 8 * 1024 * 1024)

    def fake_charged(mountpoint, sources, container_root_device):
        del sources, container_root_device
        return mountpoint == str(present)

    # One mount point per root, so each can be classified on its own.
    monkeypatch.setattr(local_disk, '_mountpoint', lambda path: path)
    monkeypatch.setattr(local_disk, '_charged_to_ephemeral', fake_charged)
    monkeypatch.setenv(local_disk.EPHEMERAL_STORAGE_LIMIT_ENV_VAR, str(1024**3))

    snapshot = local_disk.scan()

    assert snapshot.is_charged_to_ephemeral('present') is True
    assert snapshot.is_charged_to_ephemeral('other') is False
    # 'other' holds 8 MB and must not appear in the total.
    assert snapshot.used_bytes == snapshot.roots['present'].used_bytes
    assert snapshot.used_bytes < 8 * 1024 * 1024
    assert snapshot.headroom_bytes == 1024**3 - snapshot.used_bytes
    # Both roots still report their own size; only the aggregate is scoped.
    assert snapshot.roots['other'].used_bytes >= 8 * 1024 * 1024

    charged = {
        s.labels['root']: s.value
        for s in _families(metrics.LocalDiskUsageCollector())
        ['sky_apiserver_local_disk_root_charged_to_ephemeral'].samples
    }
    assert charged == {'present': 1.0, 'other': 0.0}


def test_charging_follows_the_device_then_the_mount_source(tmp_path):
    mountpoint = str(tmp_path)
    device = os.stat(mountpoint).st_dev

    # Same device as the container's writable layer.
    assert local_disk._charged_to_ephemeral(mountpoint, {}, device) is True

    # A different device, but a local scratch volume by its mount source.
    scratch = {
        mountpoint: ('/var/lib/kubelet/pods/abc/volumes/'
                     f'{local_disk._EPHEMERAL_VOLUME_MARKER}/sky-ephemeral')
    }
    assert local_disk._charged_to_ephemeral(mountpoint, scratch,
                                            device + 1) is True

    # A different device and an unrecognised source: a persistent volume as
    # far as this container can tell, so not charged. Erring this way misses
    # a warning rather than raising a false one.
    pvc = {mountpoint: '/nfs-export/tenant/api_server/clients'}
    assert local_disk._charged_to_ephemeral(mountpoint, pvc,
                                            device + 1) is False
    assert local_disk._charged_to_ephemeral(mountpoint, {}, device + 1) is False


def test_charging_a_path_that_is_not_there(tmp_path):
    assert local_disk._charged_to_ephemeral(str(tmp_path / 'gone'), {},
                                            None) is False


def test_deadline_is_enforced_on_a_small_tree(roots):
    """A slow filesystem must not outrun the timeout on a small tree.

    Before this, the deadline was only re-checked every few thousand
    entries, so a handful of files on a filesystem with millisecond stat
    latency ran to completion and reported an untruncated result.
    """
    present, _ = roots
    for i in range(4):
        _write(str(present / f'{i}.log'), 1024)

    snapshot = local_disk.scan(timeout_seconds=-1.0)

    assert snapshot.roots['present'].truncated is True


def test_an_unreadable_subtree_is_reported_not_hidden(roots):
    """A permission error must not publish a partial size as a whole one."""
    present, _ = roots
    _write(str(present / 'readable.log'), 4096)
    locked = present / 'locked'
    locked.mkdir()
    _write(str(locked / 'big.log'), 1024 * 1024)
    os.chmod(locked, 0o000)
    try:
        usage = local_disk.scan().roots['present']
    finally:
        os.chmod(locked, 0o755)

    assert usage.unreadable == 1
    # The megabyte inside the unreadable directory is missing, which is
    # exactly why the count has to be published alongside the size.
    assert usage.used_bytes < 1024 * 1024
    assert usage.truncated is False


def test_a_vanished_entry_is_not_an_unreadable_one(roots):
    """The request-log GC unlinks constantly; those bytes really are gone."""
    present, _ = roots
    _write(str(present / 'a.log'), 4096)

    assert local_disk.scan().roots['present'].unreadable == 0
    assert local_disk._count_unreadable(OSError(errno.ENOENT, 'gone')) == 0
    assert local_disk._count_unreadable(OSError(errno.EACCES, 'denied')) == 1


def test_a_root_inside_another_root_is_measured_once(tmp_path, monkeypatch):
    """Overlapping roots would count every byte in the overlap twice.

    Hard links are only deduplicated within one root, so an overlap
    corrupts the total rather than merely duplicating a label.
    """
    outer = tmp_path / 'outer'
    inner = outer / 'nested' / 'inner'
    inner.mkdir(parents=True)
    _write(str(inner / 'a.log'), 512 * 1024)

    monkeypatch.setattr(
        local_disk, 'local_roots', lambda: local_disk._drop_nested({
            'outer': str(outer),
            'inner': str(inner),
            'same_as_outer': str(outer),
        }))

    snapshot = local_disk.scan()

    assert list(snapshot.roots) == ['outer']
    assert snapshot.roots['outer'].files == 1
