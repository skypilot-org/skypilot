"""Tests for token handling in sky/utils/git_clone.sh."""

import os
import pathlib
import shutil
import stat
import subprocess
import sys
from typing import Dict, List

import pytest

_SCRIPT = (pathlib.Path(__file__).resolve().parents[3] / 'sky' / 'utils' /
           'git_clone.sh')

# Stand-in for git that records each call: a marker line, then one line per
# argument.
_GIT_SHIM = ('#!/bin/sh\n'
             '{ echo "--- call"; for a in "$@"; do echo "$a"; done; }'
             ' >> "$GIT_SHIM_LOG"\n')

_requires_bash_and_git = pytest.mark.skipif(
    sys.platform == 'win32' or shutil.which('bash') is None or
    shutil.which('git') is None,
    reason='git_clone.sh needs bash and git')


def _run_script(tmp_path: pathlib.Path) -> List[List[str]]:
    """Runs git_clone.sh with a fake git on PATH; returns the recorded calls."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'git_calls.log'
    shim = bin_dir / 'git'
    shim.write_text(_GIT_SHIM)
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    env = dict(os.environ,
               PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
               GIT_SHIM_LOG=str(log),
               GIT_URL='https://github.com/owner/private.git',
               GIT_TOKEN='secret-token',
               GIT_BRANCH='main')
    subprocess.run(
        ['bash', str(_SCRIPT), str(tmp_path / 'workdir')],
        env=env,
        check=True,
        capture_output=True)
    chunks = log.read_text().split('--- call\n')
    return [chunk.splitlines() for chunk in chunks if chunk]


def _git_config_options(call: List[str]) -> List[str]:
    """The `-c key=value` options of a recorded git call."""
    return [call[i + 1] for i, arg in enumerate(call[:-1]) if arg == '-c']


def _credential_fill(options: List[str], host: str) -> Dict[str, str]:
    """Asks the real git which credential `options` give for `host`."""
    cmd = ['git']
    for option in options:
        cmd += ['-c', option]
    result = subprocess.run(
        cmd + ['credential', 'fill'],
        input=f'protocol=https\nhost={host}\n\n',
        # No prompting: git fails if no helper supplies a credential.
        env=dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_TOKEN='secret-token'),
        capture_output=True,
        text=True,
        check=False)
    return dict(
        line.split('=', 1)
        for line in result.stdout.splitlines()
        if '=' in line)


@_requires_bash_and_git
def test_token_is_not_embedded_in_the_url(tmp_path):
    """Newer libcurl strips URL credentials, which breaks private clones."""
    calls = _run_script(tmp_path)
    assert any('clone' in call for call in calls)
    assert not any('secret-token' in arg for call in calls for arg in call)


@_requires_bash_and_git
def test_credential_helper_is_scoped_to_the_repo_host(tmp_path):
    clone = next(call for call in _run_script(tmp_path) if 'clone' in call)
    options = _git_config_options(clone)

    own_host = _credential_fill(options, 'github.com')
    assert own_host.get('username') == 'secret-token'
    assert own_host.get('password') == ''
    # Another host, such as a cross-host redirect target, gets nothing.
    assert 'secret-token' not in _credential_fill(options,
                                                  'other.example').values()


@_requires_bash_and_git
def test_credential_helper_is_kept_for_later_git_commands(tmp_path):
    calls = _run_script(tmp_path)
    persisted = [
        call for call in calls if call[:3] == ['config', '--global', '--add']
    ]
    keys = {call[3] for call in persisted}
    assert keys == {'credential.https://github.com.helper'}
    # An empty entry first resets any helper inherited from other config.
    assert persisted[0][4] == ''
    # The persisted helper reads the token from the environment; it never
    # holds the token itself.
    assert not any('secret-token' in arg for call in persisted for arg in call)
    options = [f'{call[3]}={call[4]}' for call in persisted]
    assert _credential_fill(options,
                            'github.com').get('username') == ('secret-token')
