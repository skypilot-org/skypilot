"""Unit tests for the apt hardening in the fluent-bit setup command."""

import re
import unittest

from sky.logs.agent import FluentbitAgent
from sky.utils import resources_utils


class _Agent(FluentbitAgent):
    """Concrete FluentbitAgent so get_setup_command can be rendered."""

    def fluentbit_output_config(self, cluster_name):
        del cluster_name  # unused in the stub
        return {'name': 'stdout', 'match': '*'}

    def get_credential_file_mounts(self):
        return {}


class TestFluentbitAptHardening(unittest.TestCase):
    """The fluent-bit install must not hang a launch on a bad distro mirror."""

    def setUp(self):
        self.cmd = _Agent().get_setup_command(
            resources_utils.ClusterName('test-cluster', 'test-cluster'))

    def test_every_apt_call_is_bounded(self):
        """No apt-get may run unbounded on a wedged mirror."""
        # Every apt-get goes through the helper, so there is no bare
        # `sudo apt-get` anywhere -- one would bypass both the mirror fallback
        # and the locale pinning.
        self.assertEqual(self.cmd.count('sudo apt-get'), 0)
        self.assertEqual(self.cmd.count('sudo env LC_ALL=C apt-get'), 2)
        self.assertNotIn('sky_apt_run 120 update', self.cmd)
        self.assertIn('timeout "$_t" sudo env LC_ALL=C apt-get', self.cmd)
        self.assertIn('sky_apt_run 600 update', self.cmd)
        # `install` is deliberately NOT wall-clock capped: a SIGTERM landing in
        # dpkg unpack/configure would leave the package database broken.
        self.assertIn('sky_apt_run "" install -y', self.cmd)
        self.assertNotIn('sky_apt_run 300 install', self.cmd)

    def test_exit_status_is_not_trusted(self):
        """apt-get update exits 0 even when all sources fail to fetch."""
        self.assertIn('Failed to fetch', self.cmd)

    def test_retries_are_not_delegated_to_apt(self):
        """In-apt retries multiply the stall; the useful retry swaps mirror."""
        self.assertIn('Acquire::Retries=0', self.cmd)

    def test_falls_back_to_the_canonical_archive(self):
        self.assertIn('sky_apt_use_fallback', self.cmd)
        self.assertIn('http://archive.ubuntu.com/ubuntu', self.cmd)
        self.assertIn('http://security.ubuntu.com/ubuntu', self.cmd)
        # The fallback is selected for our apt calls only...
        self.assertIn('-o Dir::Etc::sourcelist=$sky_apt_dir/archive.list',
                      self.cmd)
        # ...and sourceparts must point at OUR directory, not the node's. On
        # deb822 images (Ubuntu 24.04+) the distro archive lives in
        # /etc/apt/sources.list.d/ubuntu.sources, so keeping the node's
        # sources.list.d selected would still consult the dead mirror.
        self.assertIn('-o Dir::Etc::sourceparts=$sky_apt_dir/sources.list.d',
                      self.cmd)
        self.assertNotIn('Dir::Etc::sourceparts=/etc/apt/sources.list.d',
                         self.cmd)
        # SkyPilot's own repo lists are carried across into the fallback config.
        self.assertIn('sky_apt_sync_own_lists', self.cmd)

    def test_node_sources_list_is_never_rewritten(self):
        """An operator's pinned or offline mirror stays authoritative."""
        # Ignore comment lines, which legitimately mention the path.
        code = '\n'.join(line for line in self.cmd.splitlines()
                         if not line.lstrip().startswith('#'))
        # Nothing may write to /etc/apt/sources.list itself. Writing into
        # sources.list.d/ (the fluent-bit repo) is expected and allowed.
        pattern = (r'(?:>|tee|sed -i\b[^\n]*?)\s*'
                   r'/etc/apt/sources\.list(?!\.d)')
        self.assertIsNone(re.search(pattern, code))

    def test_fallback_primes_indexes_before_retrying_an_install(self):
        """Switched-to sources have no fetched indexes yet.

        Retrying an install straight after the switch would resolve against an
        empty package universe and fail with 'Unable to locate package'.
        """
        self.assertIn('if [ "$1" != update ]; then', self.cmd)
        self.assertIn('sky_apt_exec 600 update', self.cmd)

    def test_fallback_scratch_dir_is_root_owned(self):
        """A predictable path in world-writable /tmp is a symlink target."""
        self.assertIn('/etc/apt/sky-fallback', self.cmd)
        self.assertNotIn('/tmp/sky-apt-fallback.list', self.cmd)

    def test_output_check_is_locale_independent(self):
        """apt messages are localised; the grep must not silently pass."""
        self.assertIn('env LC_ALL=C apt-get', self.cmd)

    def test_fallback_is_ubuntu_only(self):
        """Debian's default deb.debian.org is already CDN-backed."""
        self.assertIn('"$sky_apt_os_id" = ubuntu', self.cmd)

    def test_install_is_still_pinned_below_5(self):
        self.assertIn('fluent-bit=4.*', self.cmd)


if __name__ == '__main__':
    unittest.main()
