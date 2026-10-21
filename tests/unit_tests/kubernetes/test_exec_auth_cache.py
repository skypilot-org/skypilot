"""Tests for the API server's shared `aws eks get-token` cache."""
import os
import stat
import subprocess
import textwrap
import time
from typing import Dict, List, Optional

import pytest

from sky.skylet import constants
from sky.utils.kubernetes import exec_auth_cache

_GET_TOKEN = [
    'aws', '--region', 'us-east-1', 'eks', 'get-token', '--cluster-name', 'c1'
]

_FAKE_AWS = textwrap.dedent("""\
    #!/bin/bash
    echo "$*" >> "$FAKE_AWS_LOG"
    rc=$(cat "$FAKE_AWS_RC" 2>/dev/null || echo 0)
    if [[ "$rc" != 0 ]]; then
        echo "fake aws failure" >&2
        exit "$rc"
    fi
    sleep "${FAKE_AWS_SLEEP:-0}"
    n=$(wc -l < "$FAKE_AWS_LOG")
    exp=$(date -u -d "@$(( $(date +%s) + 840 ))" +%Y-%m-%dT%H:%M:%SZ)
    printf '{"kind": "ExecCredential", "status": '
    printf '{"expirationTimestamp": "%s", "token": "token-%s"}}\\n' "$exp" "$n"
    """)


class _Rig:
    """Runs the installed front against a fake `aws` that logs each call."""

    def __init__(self, tmp_path):
        self.log = tmp_path / 'calls.log'
        self.rc_file = tmp_path / 'rc'
        self.log.touch()
        self.cache_dir = os.path.join(exec_auth_cache.cache_root(), 'cache')

    def run(self,
            args: Optional[List[str]] = None,
            extra_env: Optional[Dict[str, str]] = None,
            sleep: float = 0) -> subprocess.CompletedProcess:
        return subprocess.run(args or _GET_TOKEN,
                              env=self.env(extra_env, sleep),
                              capture_output=True,
                              text=True,
                              check=False)

    def env(self, extra_env: Optional[Dict[str, str]] = None, sleep: float = 0):
        env = {k: v for k, v in os.environ.items() if not k.startswith('AWS_')}
        env.update(FAKE_AWS_LOG=str(self.log),
                   FAKE_AWS_RC=str(self.rc_file),
                   FAKE_AWS_SLEEP=str(sleep))
        env.update(extra_env or {})
        return env

    def calls(self) -> List[str]:
        return self.log.read_text().splitlines()

    def fail_with(self, rc: int) -> None:
        self.rc_file.write_text(str(rc))

    def entries(self) -> List[str]:
        return [
            os.path.join(self.cache_dir, name)
            for name in os.listdir(self.cache_dir)
            if not name.endswith(('.lock', '.tmp'))
        ]

    def set_entry_times(self, fetched_ago: int, expires_in: int) -> None:
        [entry] = self.entries()
        with open(entry, 'r', encoding='utf-8') as f:
            _, body = f.read().split('\n', 1)
        now = int(time.time())
        with open(entry, 'w', encoding='utf-8') as f:
            f.write(f'{now - fetched_ago} {now + expires_in}\n{body}')


@pytest.fixture
def rig(tmp_path, monkeypatch):
    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    fake_aws = fake_bin / 'aws'
    fake_aws.write_text(_FAKE_AWS)
    fake_aws.chmod(0o755)
    tmp_root = tmp_path / 'tmp'
    tmp_root.mkdir()
    monkeypatch.setattr(exec_auth_cache.tempfile, 'gettempdir',
                        lambda: str(tmp_root))
    monkeypatch.setenv('PATH', f'{fake_bin}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.delenv(constants.ENV_VAR_SERVER_EXEC_AUTH_CACHE, raising=False)
    real_aws = exec_auth_cache.install()
    if real_aws is None:
        pytest.skip('Host lacks bash >= 5, flock, sha256sum or GNU date.')
    assert real_aws == str(fake_aws)
    assert os.environ['PATH'].split(os.pathsep)[0] == exec_auth_cache.bin_dir()
    return _Rig(tmp_path)


def test_concurrent_calls_share_one_fetch(rig):
    procs = [
        subprocess.Popen(_GET_TOKEN,
                         env=rig.env(sleep=1),
                         stdout=subprocess.PIPE,
                         text=True) for _ in range(50)
    ]
    outputs = [p.communicate()[0] for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert len(rig.calls()) == 1
    assert set(outputs) == {outputs[0]}
    assert '"token": "token-1"' in outputs[0]


def test_args_and_aws_env_select_the_entry(rig):
    assert rig.run().returncode == 0
    assert rig.run().returncode == 0
    assert len(rig.calls()) == 1
    rig.run(extra_env={'AWS_PROFILE': 'a'})
    rig.run(extra_env={'AWS_PROFILE': 'b'})
    rig.run(extra_env={'AWS_PROFILE': 'a'})
    assert len(rig.calls()) == 3
    other_cluster = _GET_TOKEN[:-1] + ['c2']
    rig.run(other_cluster)
    assert len(rig.calls()) == 4
    assert len(rig.entries()) == 4


def test_failure_is_not_cached(rig):
    rig.fail_with(255)
    first = rig.run()
    assert first.returncode == 255
    assert 'fake aws failure' in first.stderr
    assert rig.run().returncode == 255
    assert len(rig.calls()) == 2
    assert not rig.entries()
    rig.fail_with(0)
    assert rig.run().returncode == 0
    assert rig.run().returncode == 0
    assert len(rig.calls()) == 3


def test_stale_entry_is_refreshed(rig):
    rig.run()
    rig.set_entry_times(fetched_ago=400, expires_in=440)
    result = rig.run()
    assert result.returncode == 0
    assert '"token": "token-2"' in result.stdout
    assert len(rig.calls()) == 2
    assert '"token": "token-2"' in rig.run().stdout
    assert len(rig.calls()) == 2


def test_failed_refresh_serves_the_stale_token(rig):
    rig.run()
    rig.set_entry_times(fetched_ago=400, expires_in=440)
    rig.fail_with(255)
    result = rig.run()
    assert result.returncode == 0
    assert '"token": "token-1"' in result.stdout
    assert 'using the cached token' in result.stderr
    assert len(rig.calls()) == 2


def test_entry_near_expiry_is_not_served(rig):
    rig.run()
    rig.set_entry_times(fetched_ago=800, expires_in=30)
    rig.fail_with(255)
    assert rig.run().returncode == 255
    assert len(rig.calls()) == 2


def test_other_commands_pass_through(rig):
    for _ in range(2):
        result = rig.run(['aws', 's3', 'ls', 's3://bucket'])
        assert result.returncode == 0
    assert rig.calls() == ['s3 ls s3://bucket'] * 2
    assert not os.path.exists(rig.cache_dir) or not rig.entries()


def test_cache_files_are_private(rig):
    rig.run()
    root = exec_auth_cache.cache_root()
    for path in (root, exec_auth_cache.bin_dir(), rig.cache_dir):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    [entry] = rig.entries()
    assert stat.S_IMODE(os.stat(entry).st_mode) == 0o600


def test_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setattr(exec_auth_cache.tempfile, 'gettempdir',
                        lambda: str(tmp_path))
    monkeypatch.setenv(constants.ENV_VAR_SERVER_EXEC_AUTH_CACHE, '0')
    path_before = os.environ['PATH']
    assert exec_auth_cache.install() is None
    assert os.environ['PATH'] == path_before
    assert not os.path.exists(exec_auth_cache.cache_root())


def test_refuses_a_shared_cache_root(tmp_path, monkeypatch):
    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    (fake_bin / 'aws').write_text(_FAKE_AWS)
    (fake_bin / 'aws').chmod(0o755)
    monkeypatch.setattr(exec_auth_cache.tempfile, 'gettempdir',
                        lambda: str(tmp_path))
    monkeypatch.setenv('PATH', f'{fake_bin}{os.pathsep}{os.environ["PATH"]}')
    os.mkdir(exec_auth_cache.cache_root(), 0o777)
    os.chmod(exec_auth_cache.cache_root(), 0o777)
    path_before = os.environ['PATH']
    assert exec_auth_cache.install() is None
    assert os.environ['PATH'] == path_before
