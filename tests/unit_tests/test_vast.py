"""Unit tests for the Vast provisioner."""
from sky.provision.vast import utils


def test_register_ssh_key_attaches_to_the_instance(monkeypatch):
    """The key must be registered with Vast, not only appended by onstart.

    Vast owns the instance's authorized set and drops what it did not register,
    which strands a running cluster with `Permission denied (publickey)`.
    """
    calls = []

    class _Client:

        def attach_ssh(self, instance_id, ssh_key):
            calls.append((instance_id, ssh_key))
            return {'success': True}

    monkeypatch.setattr(utils.vast, 'vast', lambda: _Client())
    utils._register_ssh_key(42, '  ssh-rsa AAAA  ')
    assert calls == [(42, 'ssh-rsa AAAA')]


def test_register_ssh_key_warns_instead_of_raising(monkeypatch, caplog):
    """A launch that works now must not abort, but the failure must be visible.

    Registering on the account already fails silently for team API keys;
    repeating that here would hide the same outage.
    """

    class _Client:

        def attach_ssh(self, instance_id, ssh_key):
            raise RuntimeError('boom')

    monkeypatch.setattr(utils.vast, 'vast', lambda: _Client())
    with caplog.at_level('WARNING'):
        utils._register_ssh_key(42, 'ssh-rsa AAAA')
    assert 'boom' in caplog.text
