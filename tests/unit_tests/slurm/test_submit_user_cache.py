"""Slurm reads for requests that share a process and submit as other users."""
import contextvars
from typing import Optional

import pytest

from sky.provision.slurm import utils as slurm_utils
from sky.utils import annotations
from sky.utils import context

_CLUSTER = 'test-slurm'

_submit_user: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'submit_user', default=None)


class _SSHConfig:

    def lookup(self, cluster):
        del cluster
        return {'hostname': 'login', 'user': 'sky'}


class _SlurmClient:
    """Answers as Slurm does: what the submit user may see."""

    def __init__(self, *args, slurm_user: Optional[str] = None, **kwargs):
        del args, kwargs
        self.slurm_user = slurm_user

    def info_nodes(self):
        return [f'node-visible-to-{self.slurm_user}']

    def get_default_partition(self):
        return f'partition-of-{self.slurm_user}'


def _request(user: str) -> contextvars.Context:
    """A request submitted as `user`."""
    request = contextvars.Context()

    def start():
        context.initialize()
        _submit_user.set(user)

    request.run(start)
    return request


@pytest.fixture(autouse=True)
def _slurm(monkeypatch):
    monkeypatch.setattr(slurm_utils, 'get_slurm_ssh_config', _SSHConfig)
    monkeypatch.setattr(slurm_utils, 'get_submit_user',
                        lambda cluster: _submit_user.get())
    monkeypatch.setattr(slurm_utils.slurm, 'SlurmClient', _SlurmClient)
    monkeypatch.setattr(slurm_utils.kv_cache, 'get_cache_entry',
                        lambda key: None)
    monkeypatch.setattr(slurm_utils.kv_cache, 'add_or_update_cache_entry',
                        lambda *args: None)
    annotations.clear_request_level_cache()
    yield
    annotations.clear_request_level_cache()


def test_nodes_info_is_kept_per_submit_user():
    alice = _request('alice')
    bob = _request('bob')

    def nodes_info():
        return slurm_utils.get_slurm_nodes_info(_CLUSTER)

    assert alice.run(nodes_info) == ['node-visible-to-alice']
    assert bob.run(nodes_info) == ['node-visible-to-bob']
    assert alice.run(nodes_info) == ['node-visible-to-alice']


def test_default_partition_is_kept_per_submit_user():
    alice = _request('alice')
    bob = _request('bob')

    def default_partition():
        return slurm_utils.get_cluster_default_partition(_CLUSTER)

    assert alice.run(default_partition) == 'partition-of-alice'
    assert bob.run(default_partition) == 'partition-of-bob'
    assert alice.run(default_partition) == 'partition-of-alice'
