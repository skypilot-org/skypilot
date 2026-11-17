"""Realtime GPU availability: empty results and unreachable contexts."""
from unittest import mock

import pytest

from sky import core
from sky import models
from sky.provision.kubernetes import utils as kubernetes_utils


def _down_is_unreachable(context):
    return 'proxy is down' if context == 'down' else None


@pytest.fixture(autouse=True)
def _unreachable_checks(monkeypatch):
    monkeypatch.setattr(kubernetes_utils, '_CONTEXT_UNREACHABLE_CHECKS',
                        [_down_is_unreachable])


@pytest.fixture
def realtime_catalog(monkeypatch):
    queried = []

    def list_accelerator_realtime(**kwargs):
        queried.append(kwargs['region_filter'])
        return {'H100': [1, 2]}, {'H100': 8}, {'H100': 4}

    monkeypatch.setattr(core.catalog, 'list_accelerator_realtime',
                        list_accelerator_realtime)
    return queried


def test_requested_unreachable_context_raises_its_reason(realtime_catalog):
    with pytest.raises(ValueError,
                       match="'down' is unreachable: proxy is down"):
        core.realtime_kubernetes_gpu_availability(context='down')
    assert realtime_catalog == []


def test_all_contexts_unreachable_raises_their_reasons(realtime_catalog):
    with mock.patch.object(core.clouds.Kubernetes,
                           'existing_allowed_contexts',
                           return_value=['down']), \
            mock.patch.object(core.clouds.SSH,
                              'existing_allowed_contexts',
                              return_value=[]):
        with pytest.raises(ValueError,
                           match="'down' is unreachable: proxy is down"):
            core.realtime_kubernetes_gpu_availability()
    assert realtime_catalog == []


def test_all_contexts_leave_out_unreachable_ones(realtime_catalog):
    with mock.patch.object(core.clouds.Kubernetes,
                           'existing_allowed_contexts',
                           return_value=['up', 'down']), \
            mock.patch.object(core.clouds.SSH,
                              'existing_allowed_contexts',
                              return_value=[]):
        result = core.realtime_kubernetes_gpu_availability()
    assert realtime_catalog == ['up']
    assert result == [('up',
                       [models.RealtimeGpuAvailability('H100', [1, 2], 8, 4)])]


@pytest.fixture
def no_gpu_catalog(monkeypatch):
    queried = []

    def list_accelerator_realtime(**kwargs):
        queried.append(kwargs['region_filter'])
        return {}, {}, {}

    monkeypatch.setattr(core.catalog, 'list_accelerator_realtime',
                        list_accelerator_realtime)
    return queried


@pytest.mark.parametrize('is_ssh', [False, True])
def test_requested_context_without_gpus_returns_empty(no_gpu_catalog, is_ssh):
    assert core.realtime_kubernetes_gpu_availability(context='cpu-only',
                                                     is_ssh=is_ssh) == []
    assert no_gpu_catalog == ['cpu-only']


def test_all_contexts_without_gpus_return_empty(no_gpu_catalog):
    with mock.patch.object(core.clouds.Kubernetes,
                           'existing_allowed_contexts',
                           return_value=['cpu-a', 'cpu-b', 'down']), \
            mock.patch.object(core.clouds.SSH,
                              'existing_allowed_contexts',
                              return_value=[]):
        result = core.realtime_kubernetes_gpu_availability(name_filter='H100',
                                                           quantity_filter=8)
    assert result == []
    assert no_gpu_catalog == ['cpu-a', 'cpu-b']
