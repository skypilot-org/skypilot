"""Tests for provision.get_initial_runtime_metadata."""
import types

from sky import provision
from sky.provision import common


def test_default_is_ray_runtime():
    metadata = provision.get_initial_runtime_metadata('kubernetes',
                                                      {'context': 'ctx'})
    assert metadata == common.ProvisionRuntimeMetadata()


def test_registered_provisioner_declares_runtime(monkeypatch):
    no_ray = common.ProvisionRuntimeMetadata(has_ray=False,
                                             has_skylet=False,
                                             has_job_queue=False)
    received = []

    def get_initial_runtime_metadata(provider_config):
        received.append(provider_config)
        return no_ray

    monkeypatch.setattr(provision, '_registered_provisioners', {})
    provision.register_provisioner(
        'kubernetes',
        types.SimpleNamespace(
            get_initial_runtime_metadata=get_initial_runtime_metadata))

    provider_config = {'context': 'ctx'}
    assert provision.get_initial_runtime_metadata('Kubernetes',
                                                  provider_config) == no_ray
    assert received == [provider_config]
