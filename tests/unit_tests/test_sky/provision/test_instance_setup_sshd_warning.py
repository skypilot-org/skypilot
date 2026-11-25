"""setup_runtime_on_cluster warns once for the nodes that have no sshd."""
from unittest import mock

import pytest

from sky.provision import instance_setup


def _warnings(monkeypatch, outputs):
    """Runs setup on one node per entry; None stands for a cached node."""

    def _parallel(func, *args, **kwargs):
        del args, kwargs
        return [
            None if out is None else func(
                mock.Mock(run_setup=mock.Mock(return_value=(0, out,
                                                            ''))), '/tmp/l.log')
            for out in outputs
        ]

    logger = mock.Mock()
    monkeypatch.setattr(instance_setup, 'logger', logger)
    monkeypatch.setattr(instance_setup, '_hint_worker_log_path', mock.Mock())
    monkeypatch.setattr(instance_setup, '_parallel_ssh_with_cache', _parallel)
    instance_setup.setup_runtime_on_cluster('c', ['cmd1', 'cmd2'], mock.Mock(),
                                            {})
    return [c.args[0] for c in logger.warning.call_args_list]


_NO_SSHD = f'step log\n{instance_setup.NO_SSHD_MARKER} and could not be.\n'


@pytest.mark.parametrize('outputs,expected', [
    ([_NO_SSHD, 'ok', _NO_SSHD], '2 of 3 nodes'),
    ([_NO_SSHD, None, 'ok'], '1 of 2 nodes'),
])
def test_one_warning_counts_the_nodes_without_sshd(monkeypatch, outputs,
                                                   expected):
    warnings = _warnings(monkeypatch, outputs)
    assert len(warnings) == 1
    assert warnings[0].startswith(expected)


@pytest.mark.parametrize('outputs', [['ok', 'ok'], [None, None]])
def test_no_warning_when_every_node_has_sshd(monkeypatch, outputs):
    assert not _warnings(monkeypatch, outputs)
