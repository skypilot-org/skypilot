"""Tests for _TRANSIENT_SSH_FAILURE_PATTERN in backend_utils.

The pattern must catch momentary proxy drops (e.g. SSH over SSM) while NOT
catching 'timed out', which maps to the changed-IP recovery hint on manually
restarted clusters (_SSH_CONNECTION_TIMED_OUT_PATTERN).
"""
from sky.backends.backend_utils import _SSH_CONNECTION_TIMED_OUT_PATTERN
from sky.backends.backend_utils import _TRANSIENT_SSH_FAILURE_PATTERN


class TestTransientSshFailurePattern:
    """Retryable transport failures vs. genuine failures."""

    def test_ssm_target_not_connected_is_transient(self):
        # Observed via an SSM ProxyCommand: the agent dropped for a moment
        # while the instance and ray stayed healthy.
        stderr = ('An error occurred (TargetNotConnected) when calling the '
                  'StartSession operation: i-03ec6e6553dd78951 is not '
                  'connected.')
        assert _TRANSIENT_SSH_FAILURE_PATTERN.search(stderr) is not None

    def test_kex_exchange_is_transient(self):
        stderr = ('kex_exchange_identification: Connection closed by remote '
                  'host')
        assert _TRANSIENT_SSH_FAILURE_PATTERN.search(stderr) is not None

    def test_connection_reset_is_transient(self):
        stderr = 'client_loop: send disconnect: Connection reset by peer'
        assert _TRANSIENT_SSH_FAILURE_PATTERN.search(stderr) is not None

    def test_broken_pipe_is_transient(self):
        stderr = 'client_loop: send disconnect: Broken pipe'
        assert _TRANSIENT_SSH_FAILURE_PATTERN.search(stderr) is not None

    def test_timed_out_is_not_transient(self):
        # "timed out" must keep flowing to the changed-IP recovery hint,
        # not the retry loop.
        stderr = 'ssh: connect to host 1.2.3.4 port 22: Connection timed out'
        assert _TRANSIENT_SSH_FAILURE_PATTERN.search(stderr) is None
        assert _SSH_CONNECTION_TIMED_OUT_PATTERN.search(stderr) is not None

    def test_ray_failure_is_not_transient(self):
        # A real ray-side failure must not be swallowed by the retry loop.
        output = 'Failed to check ray cluster\'s healthiness.'
        assert _TRANSIENT_SSH_FAILURE_PATTERN.search(output) is None

    def test_permission_denied_is_not_transient(self):
        stderr = 'user@1.2.3.4: Permission denied (publickey).'
        assert _TRANSIENT_SSH_FAILURE_PATTERN.search(stderr) is None
