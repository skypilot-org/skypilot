"""Unit tests for GcsStore mount commands."""

import os
import stat
import subprocess
from unittest import mock

from sky.data import mounting_utils
from sky.data import storage as storage_lib


def _get_version_check_cmd() -> str:
    store = object.__new__(storage_lib.GcsStore)
    store.bucket = mock.Mock()
    store.bucket.name = 'test-bucket'
    store._bucket_sub_path = None
    with mock.patch.object(mounting_utils,
                           'get_mounting_command') as get_mounting_command:
        store.mount_command('/mnt/test')
    return get_mounting_command.call_args.args[3]


def _run_version_check(tmp_path, banner_fd: int, version: str) -> int:
    """Runs the version check against a fake gcsfuse on PATH."""
    fake_gcsfuse = tmp_path / 'gcsfuse'
    fake_gcsfuse.write_text(
        f'#!/bin/sh\necho "gcsfuse version {version}" >&{banner_fd}\n')
    fake_gcsfuse.chmod(fake_gcsfuse.stat().st_mode | stat.S_IEXEC)
    path = os.pathsep.join([str(tmp_path), os.environ['PATH']])
    env = {**os.environ, 'PATH': path}
    return subprocess.run(['bash', '-c', _get_version_check_cmd()],
                          env=env,
                          check=False).returncode


def test_gcs_version_check_matches_banner_on_stderr(tmp_path):
    # gcsfuse writes its version banner to stderr.
    assert _run_version_check(tmp_path, 2, mounting_utils.GCSFUSE_VERSION) == 0


def test_gcs_version_check_matches_banner_on_stdout(tmp_path):
    assert _run_version_check(tmp_path, 1, mounting_utils.GCSFUSE_VERSION) == 0


def test_gcs_version_check_rejects_other_version(tmp_path):
    assert _run_version_check(tmp_path, 2, '1.3.0') != 0
