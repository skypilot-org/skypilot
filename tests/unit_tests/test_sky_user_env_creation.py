"""Unit tests for constants.SKY_USER_ENV_CREATION_COMMANDS.

The command is executed with bash against a fake $HOME whose uv is a stub, so
creation, idempotency and repair of the default user environment are checked
without network access or a real uv.
"""

import os
import pathlib
import shutil
import subprocess
from typing import Optional

import pytest

from sky.skylet import constants

# Mimics `uv venv --seed ... <target>`. STUB_UV_MODE selects the outcome:
#   ok:     create a complete env (python, pip, activate).
#   noseed: create the env directory but fail before seeding pip, like a
#           `--seed` that loses access to the package index.
_UV_STUB = """#!/bin/bash
echo "$*" >> "$STUB_LOG"
target="${@: -1}"
mkdir -p "$target/bin"
touch "$target/bin/activate"
printf '#!/bin/bash\\n' > "$target/bin/python"
chmod +x "$target/bin/python"
if [ "$STUB_UV_MODE" = "noseed" ]; then
  exit 1
fi
printf '#!/bin/bash\\n' > "$target/bin/pip"
chmod +x "$target/bin/pip"
"""


@pytest.fixture
def fake_home(tmp_path):
    home = tmp_path / 'home'
    uv_dir = home / '.local' / 'bin'
    uv_dir.mkdir(parents=True)
    uv = uv_dir / 'uv'
    uv.write_text(_UV_STUB)
    uv.chmod(0o755)
    return home


def _run(home: pathlib.Path, mode: str = 'ok', path: Optional[str] = None):
    env = {
        'HOME': str(home),
        'PATH': path if path is not None else os.environ['PATH'],
        'STUB_LOG': str(home / 'uv.log'),
        'STUB_UV_MODE': mode,
    }
    # Resolve bash up front: some cases run with a minimal PATH.
    bash = shutil.which('bash')
    assert bash is not None
    return subprocess.run(
        [bash, '-c', constants.SKY_USER_ENV_CREATION_COMMANDS],
        env=env,
        capture_output=True,
        text=True,
        check=False)


def _uv_calls(home: pathlib.Path):
    log = home / 'uv.log'
    return log.read_text().splitlines() if log.exists() else []


def _env_dir(home: pathlib.Path) -> pathlib.Path:
    return home / 'sky-user-env'


def test_creates_env_with_pip(fake_home):
    proc = _run(fake_home)
    assert proc.returncode == 0, proc.stderr
    assert (_env_dir(fake_home) / 'bin' / 'pip').exists()
    calls = _uv_calls(fake_home)
    assert len(calls) == 1
    assert '--seed' in calls[0] and '--system-site-packages' in calls[0]


def test_usable_env_is_not_recreated(fake_home):
    _run(fake_home)
    proc = _run(fake_home)
    assert proc.returncode == 0, proc.stderr
    assert len(_uv_calls(fake_home)) == 1


def test_incomplete_env_is_rebuilt(fake_home):
    # A previous `--seed` failed after creating the directory.
    bin_dir = _env_dir(fake_home) / 'bin'
    bin_dir.mkdir(parents=True)
    (bin_dir / 'activate').touch()
    (bin_dir / 'python').touch(mode=0o755)
    proc = _run(fake_home)
    assert proc.returncode == 0, proc.stderr
    assert (bin_dir / 'pip').exists()
    assert len(_uv_calls(fake_home)) == 1


def test_failed_creation_leaves_no_env(fake_home):
    proc = _run(fake_home, mode='noseed')
    # Best effort: never fails provisioning.
    assert proc.returncode == 0, proc.stderr
    assert 'Failed to create the default user Python environment' in (
        proc.stdout)
    # No partial env left behind for ACTIVATE_SKY_USER_ENV to activate.
    assert not _env_dir(fake_home).exists()


def test_skipped_without_python3(fake_home, tmp_path):
    # PATH with only the tools the command needs besides python3.
    bin_dir = tmp_path / 'minimal_bin'
    bin_dir.mkdir()
    for tool in ('env', 'rm'):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    proc = _run(fake_home, path=str(bin_dir))
    assert proc.returncode == 0, proc.stderr
    assert not _env_dir(fake_home).exists()
    assert not _uv_calls(fake_home)
