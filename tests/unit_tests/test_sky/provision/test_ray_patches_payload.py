"""Tests for the Ray-patch payload carried in the Kubernetes pod spec.

The payload exists because the pod patches Ray before the SkyPilot wheel is
uploaded, so reading the patches out of the installed `sky` would take them
from whatever `pip install skypilot` resolved to -- an unrelated release,
whose patches target an unrelated Ray version.
"""
import base64
import gzip
import io
import os
import re
import subprocess
import tarfile
import textwrap
import threading
from unittest import mock

import pytest

from sky.provision import instance_setup
from sky.skylet import constants
from sky.skylet.ray_patches import apply_patches

RAY_PATCHES_DIR = os.path.dirname(os.path.abspath(apply_patches.__file__))
TEMPLATE = os.path.join(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
    'sky', 'templates', 'kubernetes-ray.yml.j2')


def _payload_tar():
    raw = gzip.decompress(base64.b64decode(instance_setup._ray_patches_b64()))  # pylint: disable=protected-access
    return tarfile.open(fileobj=io.BytesIO(raw), mode='r')


def _payload_members():
    with _payload_tar() as tar:
        return sorted(tar.getnames())


# --------------------------------------------------------------- the payload


def test_every_patch_file_is_registered():
    """A .patch shipped but not in _PATCHES would never be applied."""
    on_disk = {
        name for name in os.listdir(RAY_PATCHES_DIR) if name.endswith('.patch')
    }
    registered = {patch_file for _, patch_file in apply_patches._PATCHES}  # pylint: disable=protected-access
    assert on_disk == registered, (
        f'unregistered patch files: {on_disk - registered}; '
        f'registered but missing from disk: {registered - on_disk}')


def test_payload_round_trips_byte_for_byte():
    """Not just the names -- a truncated or re-encoded member is a corrupt Ray."""
    with _payload_tar() as tar:
        for member in tar.getmembers():
            with open(os.path.join(RAY_PATCHES_DIR, member.name), 'rb') as f:
                expected = f.read()
            extracted = tar.extractfile(member)
            assert extracted is not None, member.name
            assert extracted.read() == expected, f'{member.name} differs'


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


def test_payload_carries_no_timestamps():
    """The payload lands in the cluster YAML.

    If it tracked mtimes the config hash would move whenever the checkout was
    touched -- and, on a multi-replica API server, would differ *between
    replicas* for the same SkyPilot version, since each installs at its own
    time. Either way that is a needless re-provision.
    """
    raw = base64.b64decode(instance_setup._ray_patches_b64())  # pylint: disable=protected-access
    assert raw[4:8] == b'\x00\x00\x00\x00', 'gzip header carries an mtime'
    with _payload_tar() as tar:
        stamped = [m.name for m in tar.getmembers() if m.mtime != 0]
    assert not stamped, f'tar members carry an mtime: {stamped}'


# --------------------------------------------------------------- the command


def test_cmd_is_a_single_line():
    """It is spliced into one shell line inside a YAML block scalar.

    b64encode never wraps (unlike encodebytes), but pin it.
    """
    assert '\n' not in instance_setup.ray_patches_cmd('9.9.9')


def test_cmd_guards_on_the_version_it_patches_for():
    """The guard and --ray-version must name the same version.

    A node running some other Ray has to be skipped: applying files generated
    for a different Ray is exactly what corrupts it.
    """
    cmd = instance_setup.ray_patches_cmd('9.9.9')
    assert 'grep 9.9.9' in cmd
    assert '--ray-version 9.9.9' in cmd


def test_cmd_says_so_when_it_skips(tmp_path):
    """A silent skip leaves "why are my Ray patches missing" unanswerable.

    Realistic trigger: an image with a baked Ray at another version, which
    RAY_INSTALLATION_COMMANDS' idempotency guard leaves in place.
    """
    target = tmp_path / 'patches'
    cmd = instance_setup.ray_patches_cmd('9.9.9').replace(
        instance_setup._RAY_PATCHES_TARGET_DIR, str(target))  # pylint: disable=protected-access
    # A node running some other Ray.
    cmd = cmd.replace(f'{constants.SKY_UV_PIP_CMD} list', 'echo "ray 1.2.3"')

    result = subprocess.run(['bash', '-c', cmd],
                            check=False,
                            capture_output=True,
                            text=True)
    assert result.returncode == 0, 'a version mismatch must not fail the launch'
    assert not target.exists(), 'nothing should have been unpacked'
    assert 'skipping' in result.stdout.lower(), (
        f'the skip produced no diagnostic: {result.stdout!r}')


def test_cmd_does_not_read_the_installed_sky():
    """The whole point: the payload, not `from sky.skylet.ray_patches`."""
    cmd = instance_setup.ray_patches_cmd('9.9.9')
    assert 'base64 -d' in cmd
    assert 'apply_patches.py' in cmd
    assert 'from sky.skylet.ray_patches' not in cmd


def test_cmd_unpacks_and_invokes_the_applier(tmp_path):
    """Run the real command with a stub python3, and check what it does."""
    stub_dir = tmp_path / 'bin'
    stub_dir.mkdir()
    recorded = tmp_path / 'argv'
    stub = stub_dir / 'python3'
    stub.write_text(
        textwrap.dedent(f"""\
            #!/bin/sh
            echo "$@" > {recorded}
            """))
    stub.chmod(0o755)

    target = tmp_path / 'patches'
    cmd = instance_setup.ray_patches_cmd(
        constants.SKY_REMOTE_RAY_VERSION).replace(
            instance_setup._RAY_PATCHES_TARGET_DIR, str(target))  # pylint: disable=protected-access
    # Make the version guard pass without a real Ray, and drop the runtime-dir
    # indirection in SKY_PYTHON_CMD so the stub is what runs.
    # No trailing `; true` here: this substring is the head of a pipeline, and
    # a `;` would sever it and feed the greps nothing.
    cmd = cmd.replace(f'{constants.SKY_UV_PIP_CMD} list',
                      f'echo "ray {constants.SKY_REMOTE_RAY_VERSION}"')
    cmd = cmd.replace(constants.SKY_PYTHON_CMD, 'python3')

    env = dict(os.environ, PATH=f'{stub_dir}:{os.environ["PATH"]}')
    result = subprocess.run(['bash', '-c', cmd],
                            check=False,
                            capture_output=True,
                            text=True,
                            env=env)
    assert result.returncode == 0, result.stderr

    unpacked = sorted(p.name for p in target.iterdir())
    assert unpacked == _payload_members()
    argv = recorded.read_text().strip()
    assert argv.endswith(
        f'apply_patches.py --ray-version {constants.SKY_REMOTE_RAY_VERSION}')


# --------------------------------------------------------------- the wiring


def test_template_uses_the_payload_command():
    """Without this, reverting the template hunk leaves every test green."""
    with open(TEMPLATE, 'r', encoding='utf-8') as f:
        template = f.read()
    assert '{{ ray_patches_cmd }}' in template
    assert 'from sky.skylet.ray_patches' not in template, (
        'the pod is reading patches out of the installed sky again')


# The other half of the wiring -- that Kubernetes actually produces the
# ray_patches_cmd deploy variable -- is asserted in
# tests/unit_tests/test_sky/clouds/test_kubernetes.py, which already has the
# fixture for calling make_deploy_resources_variables.

# --------------------------------------------------------------- the applier


def test_the_applier_patches_every_registered_file(monkeypatch):
    """All of _PATCHES, resolved before any of them is applied.

    A patch missing from the loop is a Ray file left unpatched, with nothing
    else to notice.
    """
    seen = []

    def _fake_run_patch(target, patch_file, version, use_system_patch):
        del use_system_patch
        seen.append((target, os.path.basename(patch_file), version))

    monkeypatch.setattr(apply_patches, '_run_patch', _fake_run_patch)
    monkeypatch.setattr(apply_patches, '_ensure_patch_tooling', lambda: True)
    monkeypatch.setattr(
        apply_patches, 'importlib',
        type(
            '_M', (), {
                'import_module': staticmethod(lambda name: type(
                    '_Mod', (), {'__file__': f'/fake/{name}.py'}))
            }))

    apply_patches.apply_patches('9.9.9')

    assert sorted(name for _, name, _ in seen) == sorted(
        patch_file for _, patch_file in apply_patches._PATCHES)  # pylint: disable=protected-access
    assert {version for _, _, version in seen} == {'9.9.9'}


def test_the_applier_propagates_a_failure(monkeypatch):
    """A patch that fails must not be swallowed by the thread pool."""

    def _fake_run_patch(target, patch_file, version, use_system_patch):
        del target, version, use_system_patch
        if 'worker' in patch_file:
            raise RuntimeError('boom')

    monkeypatch.setattr(apply_patches, '_run_patch', _fake_run_patch)
    monkeypatch.setattr(apply_patches, '_ensure_patch_tooling', lambda: True)
    monkeypatch.setattr(
        apply_patches, 'importlib',
        type(
            '_M', (), {
                'import_module': staticmethod(lambda name: type(
                    '_Mod', (), {'__file__': f'/fake/{name}.py'}))
            }))

    with pytest.raises(RuntimeError, match='boom'):
        apply_patches.apply_patches('9.9.9')


def test_the_applier_runs_the_patches_concurrently():
    """The barrier only clears if every patch is in flight at once.

    A sequential loop leaves the first one waiting alone, so it breaks the
    barrier on the timeout rather than passing -- which is what makes this an
    assertion about concurrency and not just about coverage.
    """
    count = len(apply_patches._PATCHES)  # pylint: disable=protected-access
    barrier = threading.Barrier(count, timeout=10)

    def _fake_run_patch(target, patch_file, version, use_system_patch):
        del target, patch_file, version, use_system_patch
        barrier.wait()

    with mock.patch.object(apply_patches, '_run_patch', _fake_run_patch), \
         mock.patch.object(apply_patches, '_ensure_patch_tooling',
                           lambda: True), \
         mock.patch.object(apply_patches.importlib, 'import_module',
                           lambda name: mock.Mock(__file__=f'/fake/{name}.py')):
        apply_patches.apply_patches('9.9.9')


def test_the_patch_tooling_is_installed_once_not_per_target(monkeypatch):
    """Every thread racing the same `yum`/`pip install` is how this breaks.

    yum's global lock makes the losers fail, so a thread can take the
    pure-python fallback while another is still installing `patch` -- applying
    .diff and .patch within one bootstrap -- and concurrent `pip install`s of a
    single distribution can leave it half-written.
    """
    calls = []

    def _fake_ensure():
        calls.append(1)
        return True

    monkeypatch.setattr(apply_patches, '_ensure_patch_tooling', _fake_ensure)
    monkeypatch.setattr(apply_patches, '_run_patch',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(apply_patches.importlib, 'import_module',
                        lambda name: mock.Mock(__file__=f'/fake/{name}.py'))

    apply_patches.apply_patches('9.9.9')

    assert len(calls) == 1, (
        f'installed the patch tooling {len(calls)} times for '
        f'{len(apply_patches._PATCHES)} patches')  # pylint: disable=protected-access


def test_a_failed_patch_carries_the_tool_output(tmp_path):
    """Six threads share one stdout, so a failure has to travel with the error.

    Otherwise the only record of why `patch` refused is interleaved with five
    other targets' output in the pod log.
    """
    if not apply_patches._have_patch_binary():  # pylint: disable=protected-access
        pytest.skip('no system `patch` binary on this machine')
    target = tmp_path / 'worker.py'
    target.write_text('print(1)\n', encoding='utf-8')

    with pytest.raises(RuntimeError) as err:
        apply_patches._run_patch(  # pylint: disable=protected-access
            str(target),
            str(tmp_path / 'missing.py.patch'),
            '9.9.9',
            use_system_patch=True)

    message = str(err.value)
    assert 'worker.py' in message
    assert 'missing.py.patch' in message, message


# ------------------------------------------- the two representations agree


def _hunks(diff_path):
    """(old_start, [(tag, text), ...]) for each hunk of a unified diff."""
    with open(diff_path, 'r', encoding='utf-8') as f:
        lines = f.read().split('\n')
    hunks, i = [], 0
    while i < len(lines):
        header = re.match(r'^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@', lines[i])
        i += 1
        if header is None:
            continue
        body = []
        while i < len(lines) and not lines[i].startswith('@@'):
            line = lines[i]
            i += 1
            if line.startswith(('---', '+++', '\\')):
                continue
            if not line and i == len(lines):  # trailing newline
                continue
            body.append((line[0] if line else ' ', line[1:] if line else ''))
        hunks.append((int(header.group(1)), body))
    return hunks


def _before_and_after(hunks):
    """Rebuild a file the diff applies to, and what it should become.

    Only the hunks' own lines are known, so the gaps between them are filled
    with numbered placeholders. `after` is built by splicing each hunk into
    `before`, back to front, so the placeholders travel across unchanged and
    the comparison is about the edit rather than the filler.
    """
    before = {}
    for old_start, body in hunks:
        lineno = old_start
        for tag, text in body:
            if tag in ' -':
                before[lineno] = text
                lineno += 1
    lines = [before.get(n, f'# filler-{n}') for n in range(1, max(before) + 1)]
    after = list(lines)
    for old_start, body in sorted(hunks, reverse=True):
        old_len = sum(1 for tag, _ in body if tag in ' -')
        after[old_start - 1:old_start - 1 +
              old_len] = [text for tag, text in body if tag in ' +']
    return lines, after


@pytest.mark.parametrize('patch_name',
                         [name for _, name in apply_patches._PATCHES])  # pylint: disable=protected-access
def test_the_patch_and_the_diff_encode_the_same_change(patch_name, tmp_path):
    """Images with and without `patch` must not run different Ray code.

    Each target ships twice -- a normal diff for the system `patch` binary and
    a unified diff for the pure-python fallback -- and regenerating them is a
    manual, two-command step (see ray_patches/__init__.py), so they can drift
    without anything noticing. Reconstruct a file from the unified diff, apply
    the *normal* one to it, and require the same result.
    """
    if not apply_patches._have_patch_binary():  # pylint: disable=protected-access
        pytest.skip('no system `patch` binary on this machine')
    diff_path = os.path.join(RAY_PATCHES_DIR,
                             patch_name.replace('.patch', '.diff'))
    before, after = _before_and_after(_hunks(diff_path))

    source = tmp_path / patch_name.replace('.py.patch', '.py')
    source.write_text('\n'.join(before) + '\n', encoding='utf-8')
    result = tmp_path / 'out.py'
    completed = subprocess.run([
        'patch', '--fuzz=0',
        str(source), '-i',
        os.path.join(RAY_PATCHES_DIR, patch_name), '-o',
        str(result)
    ],
                               check=False,
                               capture_output=True,
                               text=True)
    assert completed.returncode == 0, (
        f'{patch_name} does not apply to the file its .diff describes: '
        f'{completed.stdout}{completed.stderr}')
    assert result.read_text(encoding='utf-8').split('\n')[:-1] == after, (
        f'{patch_name} and its .diff encode different changes; regenerate '
        'both together')
