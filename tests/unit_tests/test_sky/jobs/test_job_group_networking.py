"""Unit tests for JobGroup networking script generation."""
from sky.jobs import job_group_networking


class TestWaitForNetworkingScript:
    """The wait script is only injected when in-group networking is
    required (inter_connection enabled), so failure to initialize
    networking must fail the job instead of silently continuing."""

    def test_fails_job_when_networking_not_ready(self):
        script = job_group_networking.generate_wait_for_networking_script(
            'group', ['peer1', 'peer2'])
        assert 'exit 1' in script
        assert 'inter_connection' in script
        # The old silent-fallthrough messaging must be gone.
        assert 'Continuing without full network setup' not in script

    def test_waits_for_all_peer_hostnames(self):
        script = job_group_networking.generate_wait_for_networking_script(
            'group', ['peer1', 'peer2'])
        assert 'peer1-0.group' in script
        assert 'peer2-0.group' in script

    def test_empty_without_peers(self):
        script = job_group_networking.generate_wait_for_networking_script(
            'group', [])
        assert script == ''
