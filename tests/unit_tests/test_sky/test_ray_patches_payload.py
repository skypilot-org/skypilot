"""Tests for the Ray-patch payload carried in the Kubernetes pod spec.

The payload exists because the pod patches Ray before the SkyPilot wheel is
uploaded, so reading the patches out of the installed `sky` would take them
from whatever `pip install skypilot` resolved to -- an unrelated release.
"""
import base64
import gzip
import io
import os
import tarfile

from sky.provision import instance_setup
from sky.skylet.ray_patches import apply_patches

RAY_PATCHES_DIR = os.path.dirname(os.path.abspath(apply_patches.__file__))


def _payload_members():
    raw = gzip.decompress(base64.b64decode(instance_setup._ray_patches_b64()))  # pylint: disable=protected-access
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r') as tar:
        return sorted(tar.getnames())


def test_every_patch_file_is_registered():
    """A .patch added to the directory but not to _PATCHES would never apply."""
    on_disk = {
        name for name in os.listdir(RAY_PATCHES_DIR)
        if name.endswith('.py.patch')
    }
    registered = {patch_file for _, patch_file in apply_patches._PATCHES}  # pylint: disable=protected-access
    assert on_disk == registered, (
        f'unregistered patch files: {on_disk - registered}; '
        f'registered but missing from disk: {registered - on_disk}')


def test_payload_carries_the_applier_and_every_patch():
    members = _payload_members()
    assert 'apply_patches.py' in members
    for _, patch_file in apply_patches._PATCHES:  # pylint: disable=protected-access
        assert patch_file in members, f'{patch_file} missing from the payload'
        diff_file = patch_file.replace('.patch', '.diff')
        assert diff_file in members, f'{diff_file} missing from the payload'


def test_payload_excludes_init():
    """__init__.py imports sky, the dependency the payload exists to avoid."""
    assert '__init__.py' not in _payload_members()


def test_payload_ignores_source_mtimes():
    """The payload lands in the cluster YAML.

    If it tracked file mtimes, the config hash would change whenever the
    checkout was touched, forcing a pointless re-provision. Two calls in the
    same second would agree even with mtimes embedded, so move a real mtime
    between the calls.
    """
    build = instance_setup._ray_patches_b64  # pylint: disable=protected-access
    victim = os.path.join(RAY_PATCHES_DIR, 'worker.py.patch')
    before = os.stat(victim)

    build.cache_clear()
    first = build()
    try:
        os.utime(victim, (before.st_atime + 4242, before.st_mtime + 4242))
        build.cache_clear()
        second = build()
    finally:
        os.utime(victim, (before.st_atime, before.st_mtime))
        build.cache_clear()

    assert first == second, 'payload changed when only an mtime changed'

    # And assert the invariant directly, so the test still bites if the two
    # calls above ever coincide for some unrelated reason.
    raw = base64.b64decode(first)
    assert raw[4:8] == b'\x00\x00\x00\x00', 'gzip header carries an mtime'
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(raw)),
                      mode='r') as tar:
        stamped = [m.name for m in tar.getmembers() if m.mtime != 0]
    assert not stamped, f'tar members carry an mtime: {stamped}'


def test_cmd_unpacks_and_runs_the_applier():
    cmd = instance_setup.ray_patches_cmd('9.9.9')
    assert 'base64 -d' in cmd
    assert 'apply_patches.py' in cmd
    assert '--ray-version 9.9.9' in cmd
    # The pod must not reach for the installed package.
    assert 'from sky.skylet.ray_patches' not in cmd
