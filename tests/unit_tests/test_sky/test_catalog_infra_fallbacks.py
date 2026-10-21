"""Catalog calls without a region stay inside the active workspace.

Goes through ``catalog.list_accelerators`` with two clouds, so the per-cloud
calls run in pool threads, as they do for ``sky show-gpus``.
"""
import json

import pytest

from sky import catalog
from sky import check as sky_check
from sky import clouds
from sky import skypilot_config
from sky.catalog import config as catalog_config
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.provision.slurm import utils as slurm_utils
from sky.skylet import constants
from sky.utils import context


@pytest.fixture
def queried(tmp_path, monkeypatch):
    """Records the context and clusters the catalogs end up querying."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    config = tmp_path / 'config.yaml'
    config.write_text(
        json.dumps({
            'kubernetes': {
                'allowed_contexts': ['ctx-c']
            },
            'workspaces': {
                'ws-a': {
                    'kubernetes': {
                        'allowed_contexts': ['ctx-a']
                    },
                    'slurm': {
                        'allowed_clusters': ['sc-a']
                    },
                },
                'ws-g': {},
            },
        }))
    monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG, str(config))
    skypilot_config.reload_config()

    monkeypatch.setattr(sky_check, 'get_cached_enabled_clouds_or_refresh',
                        lambda *_, **__: [clouds.Kubernetes(),
                                          clouds.Slurm()])
    monkeypatch.setattr(kubernetes_utils, 'get_all_kube_context_names',
                        lambda: ['ctx-a', 'ctx-b', 'ctx-c'])
    monkeypatch.setattr(kubernetes_utils,
                        'get_current_kube_config_context_name', lambda: 'ctx-c')
    monkeypatch.setattr(kubernetes_utils, 'is_incluster_config_available',
                        lambda: False)
    monkeypatch.setattr(slurm_utils, 'get_all_slurm_cluster_names',
                        lambda: ['sc-a', 'sc-b'])
    seen = {'kubernetes': [], 'slurm': [], 'use_default_catalog': []}

    def check_credentials(ctx, **_):
        seen['kubernetes'].append(ctx)
        seen['use_default_catalog'].append(
            catalog_config.get_use_default_catalog_if_failed())
        return False, None  # Stop there: the context is all we need.

    def slurm_node_info(slurm_cluster_name=None):
        seen['slurm'].append(slurm_cluster_name)
        seen['use_default_catalog'].append(
            catalog_config.get_use_default_catalog_if_failed())
        return []

    monkeypatch.setattr(kubernetes_utils, 'check_credentials',
                        check_credentials)
    monkeypatch.setattr(slurm_utils, 'slurm_node_info', slurm_node_info)
    return seen


def _list_without_region():
    # Slurm raises when it finds no GPU nodes, after recording the query.
    with pytest.raises(ValueError):
        catalog.list_accelerators(clouds=['kubernetes', 'slurm'],
                                  region_filter=None)


def test_threads_use_the_active_workspace(queried):
    with skypilot_config.local_active_workspace_ctx('ws-a'):
        _list_without_region()
    # The current-context (ctx-c) is not allowed in ws-a: use ctx-a instead.
    assert queried['kubernetes'] == ['ctx-a']
    # Only ws-a's clusters, not every cluster in ~/.slurm/config.
    assert queried['slurm'] == ['sc-a']
    # list_accelerators allows the default catalog; pool threads keep that.
    assert queried['use_default_catalog'] == [True, True]


def test_threads_use_the_request_config(queried):
    context.initialize()
    # What the executor does with a request's config: a request-level view
    # that differs from the process-global one (ctx-c).
    with skypilot_config.override_skypilot_config(
        {'kubernetes': {
            'allowed_contexts': ['ctx-b']
        }}), skypilot_config.local_active_workspace_ctx('ws-g'):
        _list_without_region()
    assert queried['kubernetes'] == ['ctx-b']


def test_an_allowed_current_context_is_kept(queried):
    # ws-g inherits the global list, which has the current-context.
    with skypilot_config.local_active_workspace_ctx('ws-g'):
        _list_without_region()
    assert queried['kubernetes'] == ['ctx-c']
    assert sorted(queried['slurm']) == ['sc-a', 'sc-b']


def test_cloud_names_are_lowered_like_the_dispatch():
    assert catalog.normalize_clouds('Kubernetes',
                                    'list_accelerators') == ['kubernetes']
    assert catalog.normalize_clouds(
        None, 'list_accelerators') == [c.lower() for c in constants.ALL_CLOUDS]
    assert 'kubernetes' not in catalog.normalize_clouds(None, 'other')
