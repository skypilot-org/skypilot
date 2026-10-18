"""Tests for token handling in sky/utils/git_clone.sh."""

import os
import pathlib
import shutil
import stat
import subprocess
import sys

import pytest

_SCRIPT = (pathlib.Path(__file__).resolve().parents[3] / 'sky' / 'utils' /
           'git_clone.sh')

# Stand-in for git that records its arguments, one call per line.
_GIT_SHIM = '#!/bin/sh\nprintf "%s\\n" "$*" >> "$GIT_SHIM_LOG"\n'


@pytest.mark.skipif(sys.platform == 'win32' or shutil.which('bash') is None,
                    reason='git_clone.sh needs bash')
def test_token_is_passed_via_credential_helper_not_the_url(tmp_path):
    """The token must never be embedded in a URL passed to git.

    Newer libcurl strips credentials from the effective URL, which git reads
    as a redirect and then retries without them, so a token in the URL breaks
    private clones.
    """
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    calls = tmp_path / 'git_calls.log'
    shim = bin_dir / 'git'
    shim.write_text(_GIT_SHIM)
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    env = dict(os.environ,
               PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
               GIT_SHIM_LOG=str(calls),
               GIT_URL='https://github.com/owner/private.git',
               GIT_TOKEN='secret-token',
               GIT_BRANCH='main')
    subprocess.run(
        ['bash', str(_SCRIPT), str(tmp_path / 'workdir')],
        env=env,
        check=True,
        capture_output=True)

    logged = calls.read_text()
    assert 'clone' in logged
    assert 'secret-token' not in logged
    assert 'credential.helper=!' in logged
