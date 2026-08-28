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
import subprocess
import tarfile
import textwrap

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
