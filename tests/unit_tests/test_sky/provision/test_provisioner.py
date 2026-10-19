"""Unit tests for sky.provision.provisioner.bulk_provision."""

import contextlib
from unittest import mock

import pytest

from sky.provision import provisioner
from sky.utils import resources_utils

_CLUSTER_NAME = resources_utils.ClusterName('my-cluster', 'my-cluster-abcd1234')
_PROVIDER_CONFIG = {'namespace': 'default', 'context': 'my-context'}
_CLUSTER_YAML_DICT = {
    'head_node_type': 'ray_head_default',
    'provider': _PROVIDER_CONFIG,
    'auth': {},
    'available_node_types': {
        'ray_head_default': {
            'node_config': {}
        }
    },
}


def _run_bulk_provision(bulk_provision_error: BaseException,
                        prev_cluster_ever_up: bool = False,
                        teardown_side_effect=None) -> mock.MagicMock:
    """Runs bulk_provision with _bulk_provision raising the given error.

    Returns the mocked teardown_cluster so callers can inspect it.
    """
    with mock.patch.object(provisioner.global_user_state,
                           'get_cluster_yaml_dict',
                           return_value=_CLUSTER_YAML_DICT), \
            mock.patch.object(provisioner.provision_logging,
                              'setup_provision_logging',
                              return_value=contextlib.nullcontext()), \
            mock.patch.object(provisioner, '_bulk_provision',
                              side_effect=bulk_provision_error), \
            mock.patch.object(provisioner, 'teardown_cluster',
                              side_effect=teardown_side_effect) as teardown, \
            pytest.raises(type(bulk_provision_error)):
        provisioner.bulk_provision(cloud=mock.MagicMock(),
                                   region=mock.MagicMock(),
                                   zones=None,
                                   cluster_name=_CLUSTER_NAME,
                                   num_nodes=2,
                                   cluster_yaml='/tmp/my-cluster.yml',
                                   prev_cluster_ever_up=prev_cluster_ever_up,
                                   log_dir='/tmp/sky-logs')
    return teardown


class TestBulkProvisionInterrupted:
    """A cancelled launch must tear down what it already created."""

    def test_keyboard_interrupt_tears_down_and_reraises(self):
        teardown = _run_bulk_provision(KeyboardInterrupt())

        teardown.assert_called_once()
        _, kwargs = teardown.call_args
        assert teardown.call_args.args[1] == _CLUSTER_NAME
        assert kwargs['terminate'] is True
        assert kwargs['provider_config'] == _PROVIDER_CONFIG

    def test_system_exit_tears_down_and_reraises(self):
        teardown = _run_bulk_provision(SystemExit(1))

        teardown.assert_called_once()
        assert teardown.call_args.kwargs['terminate'] is True

    def test_previously_up_cluster_is_stopped_not_terminated(self):
        teardown = _run_bulk_provision(KeyboardInterrupt(),
                                       prev_cluster_ever_up=True)

        teardown.assert_called_once()
        assert teardown.call_args.kwargs['terminate'] is False

    def test_teardown_failure_does_not_mask_the_interrupt(self):
        # pytest.raises(KeyboardInterrupt) inside the helper is the assertion:
        # the RuntimeError from teardown must not replace the interrupt.
        teardown = _run_bulk_provision(
            KeyboardInterrupt(),
            teardown_side_effect=RuntimeError('cloud down'))

        teardown.assert_called_once()

    def test_stop_unsupported_is_tolerated(self):
        teardown = _run_bulk_provision(
            KeyboardInterrupt(),
            prev_cluster_ever_up=True,
            teardown_side_effect=NotImplementedError())

        teardown.assert_called_once()
        assert teardown.call_args.kwargs['terminate'] is False

    def test_regular_failure_keeps_failover_teardown(self):
        # Sanity check that the pre-existing failure path is untouched.
        teardown = _run_bulk_provision(RuntimeError('provision failed'))

        teardown.assert_called_once()
        assert teardown.call_args.kwargs['terminate'] is True
