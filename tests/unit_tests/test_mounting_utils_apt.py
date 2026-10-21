"""Unit tests for the apt handling in sky.data.mounting_utils."""

import os
import pathlib
import subprocess
import tempfile
import unittest

from sky.data import mounting_utils

# `apt-get update` exits non-zero when any single configured repository is
# unusable, and prints why. An end-of-life suite whose release file has
# expired is the case this stub reproduces: `update` fails, while `install`
# would still succeed from the suites that did refresh.
_APT_GET_STUB = """#!/bin/bash
for arg in "$@"; do
  case "$arg" in
    update)
      echo "E: Release file for http://example/dists/oldstable/InRelease \
is expired"
      exit 100
      ;;
    install)
      echo "install called: $*" >> "$STUB_LOG"
      exit 0
      ;;
  esac
done
exit 0
"""

_STUBS = {
    'apt-get': _APT_GET_STUB,
    'apt-cache': '#!/bin/bash\necho "libfuse3-3 - stub"\n',
    # Report x86_64 so the command does not take its arm64 early exit.
    'uname': '#!/bin/bash\n[ "$1" = "-m" ] && echo x86_64 || echo Linux\n',
    'sudo': '#!/bin/bash\nexec "$@"\n',
    # Downloading and unpacking blobfuse2 is not what is under test.
    'wget': '#!/bin/bash\nexit 0\n',
    'dpkg': '#!/bin/bash\nexit 0\n',
    'find': '#!/bin/bash\nexit 0\n',
}


class TestAzMountInstallApt(unittest.TestCase):
    """A repo the install does not need must not block the install."""

    def _run_with_stubbed_apt(self, cmd: str, tmp: pathlib.Path):
        """Runs `cmd` with apt stubbed to fail `update` and pass `install`."""
        stub_dir = tmp / 'bin'
        stub_dir.mkdir()
        for name, body in _STUBS.items():
            path = stub_dir / name
            path.write_text(body)
            path.chmod(0o755)
        log = tmp / 'calls.log'
        log.touch()
        env = dict(os.environ)
        env['PATH'] = os.pathsep.join([str(stub_dir), env.get('PATH', '')])
        # The command writes a cache dir under $HOME; keep it in the tmp dir.
        env['HOME'] = str(tmp)
        env['STUB_LOG'] = str(log)
        proc = subprocess.run(['bash', '-c', cmd],
                              env=env,
                              capture_output=True,
                              text=True,
                              timeout=120,
                              check=False)
        return proc, log.read_text()

    def test_apt_update_failure_still_reaches_the_install(self):
        """`apt-get update` failing must not skip installing fuse3.

        The Azure mount installs fuse3 and then blobfuse2. Gating that
        install on `apt-get update` meant one unusable repository -- an
        end-of-life suite with an expired release file, say -- skipped the
        install entirely, and the mount then failed on a blobfuse2 that was
        never installed. `install` is what decides instead: it fails loudly,
        naming the package it could not get.
        """
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            proc, calls = self._run_with_stubbed_apt(
                mounting_utils.get_az_mount_install_cmd(), tmp)

        self.assertIn(
            'install called', calls,
            f'the install never ran.\nstdout:\n{proc.stdout}\n'
            f'stderr:\n{proc.stderr}')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # The reason `update` failed still has to reach the log; tolerating
        # the failure must not mean hiding it.
        self.assertIn('is expired', proc.stdout)
