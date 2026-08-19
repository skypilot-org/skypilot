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
        # There is exactly one apt-get *call site* -- inside the helper, and it
        # is wrapped in `timeout`. Any additional one would be unbounded. (The
        # diagnostic message also names apt-get, but without `sudo`.)
        self.assertEqual(self.cmd.count('sudo apt-get'), 1)
        self.assertIn('timeout "$_timeout" sudo apt-get', self.cmd)
        self.assertIn('sky_apt_run 120 update', self.cmd)
        self.assertIn('sky_apt_run 300 install -y', self.cmd)

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
        self.assertIn('-o Dir::Etc::sourcelist=/tmp/sky-apt-fallback.list',
                      self.cmd)
        # ...and the fluent-bit repo in sources.list.d must stay visible.
        self.assertIn('-o Dir::Etc::sourceparts=/etc/apt/sources.list.d',
                      self.cmd)

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

    def test_fallback_is_ubuntu_only(self):
        """Debian's default deb.debian.org is already CDN-backed."""
        self.assertIn('"$sky_apt_os_id" = ubuntu', self.cmd)

    def test_install_is_still_pinned_below_5(self):
        self.assertIn('fluent-bit=4.*', self.cmd)


if __name__ == '__main__':
    unittest.main()
