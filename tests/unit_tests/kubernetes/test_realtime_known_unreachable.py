"""Realtime GPU availability and contexts known to be unreachable."""
from unittest import mock

import pytest

from sky import core
from sky import exceptions
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
    with pytest.raises(exceptions.KubeAPIUnreachableError,
                       match="'down' is unreachable: proxy is down"):
        core.realtime_kubernetes_gpu_availability(context='down')
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
