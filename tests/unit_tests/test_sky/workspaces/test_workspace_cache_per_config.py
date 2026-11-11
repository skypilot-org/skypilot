"""Workspace caches follow the config of the context that reads them.

The jobs controller is one long-lived process. It runs every job in its own
context with that job's config and never starts an API request, so nothing
clears request-scoped caches there. The workspace caches used to be keyed on
nothing but their arguments: the first job to read them fixed the workspace
list for the whole process. A job in a workspace created after that had its
own cluster filtered out of `get_clusters`, so the controller logged
"Cluster(s) not found", skipped downloading the job's logs, and then tore the
cluster down.
"""
import asyncio
from typing import Any, Dict, List, Optional, Set
from unittest import mock

import pytest

from sky import skypilot_config
from sky.backends import backend_utils
from sky.utils import annotations
from sky.utils import common
from sky.utils import config_utils
from sky.utils import context
from sky.workspaces import core as workspaces_core

# pylint: disable=protected-access


def _config(*workspaces: str) -> config_utils.Config:
    return config_utils.Config.from_dict(
        {'workspaces': {name: {} for name in workspaces}})


@pytest.fixture(autouse=True)
def _fresh_caches():
    workspaces_core._load_workspaces_for_config.cache_clear()
    workspaces_core._workspaces_for_user_and_config.cache_clear()
    yield
    workspaces_core._load_workspaces_for_config.cache_clear()
    workspaces_core._workspaces_for_user_and_config.cache_clear()


@pytest.fixture
def server_off_permissions(monkeypatch):
    # Not a server process: every requested workspace is accessible, so the
    # visible workspaces are exactly the ones in the loaded config.
    monkeypatch.delenv('IS_SKYPILOT_SERVER', raising=False)


class TestLoadedConfigKey:
    """The key identifies a loaded config object, not its content."""

    def test_same_config_gives_equal_keys(self):
        skypilot_config._set_loaded_config(_config('a'))
        first = skypilot_config.loaded_config_key()
        second = skypilot_config.loaded_config_key()
        assert first == second
        assert hash(first) == hash(second)

    def test_new_config_object_gives_a_new_key(self):
        skypilot_config._set_loaded_config(_config('a'))
        before = skypilot_config.loaded_config_key()
        # Equal content, different object: a reload always installs a new
        # object, so the key must change even when the content did not.
        skypilot_config._set_loaded_config(_config('a'))
        assert skypilot_config.loaded_config_key() != before

    def test_not_equal_to_other_types(self):
        key = skypilot_config.loaded_config_key()
        assert key != object()
        assert key != id(key)

    def test_override_gives_a_new_key_and_restores_it(self):
        skypilot_config._set_loaded_config(_config('a'))
        outer = skypilot_config.loaded_config_key()
        with skypilot_config.override_skypilot_config({'jobs': {}}):
            assert skypilot_config.loaded_config_key() != outer
        assert skypilot_config.loaded_config_key() == outer


@pytest.mark.usefixtures('server_off_permissions')
class TestSequentialReads:
    """One context whose config is reloaded with no request in between."""

    def test_reload_is_seen_without_clearing_the_cache(self):
        skypilot_config._set_loaded_config(_config('old'))
        assert 'new' not in workspaces_core._load_workspaces()
        # A reload with no request in between, as in the jobs controller.
        skypilot_config._set_loaded_config(_config('old', 'new'))
        assert set(
            workspaces_core._load_workspaces()) == {'old', 'new', 'default'}

    def test_reads_within_one_config_are_cached(self):
        skypilot_config._set_loaded_config(_config('a'))
        with mock.patch.object(skypilot_config,
                               'get_nested',
                               wraps=skypilot_config.get_nested) as get_nested:
            first = workspaces_core._load_workspaces()
            second = workspaces_core._load_workspaces()
        assert first is second
        assert get_nested.call_count == 1

    def test_alternating_configs_stay_cached(self):
        # Jobs that run at once switch between their configs on every
        # lookup; each config must keep its own entry instead of evicting
        # the others.
        configs = [_config(f'ws-{i}') for i in range(3)]
        for _ in range(5):
            for i, config in enumerate(configs):
                skypilot_config._set_loaded_config(config)
                assert f'ws-{i}' in workspaces_core._load_workspaces()
                workspaces_core.workspaces_for_user('u')
        # One miss for each config, however many switches.
        assert (workspaces_core._load_workspaces_for_config.cache_info().misses
                == len(configs))
        assert (workspaces_core._workspaces_for_user_and_config.cache_info().
                misses == len(configs))

    def test_workspaces_for_user_follows_the_config(self):
        skypilot_config._set_loaded_config(_config('old'))
        assert 'new' not in workspaces_core.workspaces_for_user('u')
        skypilot_config._set_loaded_config(_config('old', 'new'))
        assert 'new' in workspaces_core.workspaces_for_user('u')

    def test_workspaces_for_user_cached_within_one_config(self):
        skypilot_config._set_loaded_config(_config('a'))
        first = workspaces_core.workspaces_for_user('u')
        assert workspaces_core.workspaces_for_user('u') is first

    def test_request_clear_still_clears(self):
        # The executor's per-request clear must still reach both caches.
        skypilot_config._set_loaded_config(_config('a'))
        workspaces_core._load_workspaces()
        workspaces_core.workspaces_for_user('u')
        assert (workspaces_core._load_workspaces_for_config.cache_info().
                currsize == 1)
        assert (workspaces_core._workspaces_for_user_and_config.cache_info().
                currsize == 1)
        annotations.clear_request_level_cache()
        assert (workspaces_core._load_workspaces_for_config.cache_info().
                currsize == 0)
        assert (workspaces_core._workspaces_for_user_and_config.cache_info().
                currsize == 0)


def _fake_cluster_table(clusters: Dict[str, str]):
    """`global_user_state.get_clusters` over {cluster name: workspace}."""

    def get_clusters(*,
                     workspaces_filter: Optional[Set[str]] = None,
                     cluster_names: Optional[List[str]] = None,
                     **kwargs: Any) -> List[Dict[str, Any]]:
        del kwargs
        return [{
            'name': name,
            'workspace': workspace,
            'handle': None,
        }
                for name, workspace in clusters.items()
                if (cluster_names is None or name in cluster_names) and
                (workspaces_filter is None or workspace in workspaces_filter)]

    return get_clusters


@pytest.mark.usefixtures('server_off_permissions')
class TestControllerShapedLookups:
    """Jobs in one process, each in its own context with its own config."""

    @staticmethod
    def _lookup(cluster_name: str) -> List[Dict[str, Any]]:
        # The call the controller makes before downloading a finished job's
        # logs (sky/jobs/controller.py, the SUCCEEDED branch).
        return backend_utils.get_clusters(cluster_names=[cluster_name],
                                          refresh=common.StatusRefreshMode.NONE,
                                          all_users=True,
                                          _include_is_managed=True)

    def test_job_in_workspace_created_later_finds_its_cluster(self):
        """The reported failure, end to end through `get_clusters`."""
        clusters = {'job-1-cluster': 'ws-old', 'job-2-cluster': 'ws-new'}

        @context.contextual
        def run_job(config: config_utils.Config,
                    cluster_name: str) -> List[Dict[str, Any]]:
            skypilot_config._set_loaded_config(config)
            return self._lookup(cluster_name)

        with mock.patch.object(backend_utils.global_user_state, 'get_clusters',
                               _fake_cluster_table(clusters)):
            # Job 1 fills the caches before ws-new exists.
            assert run_job(_config('ws-old'), 'job-1-cluster')
            # Job 2 was submitted after ws-new was created, so its config has
            # it. Before the fix this returned [] and logged "Cluster(s) not
            # found: job-2-cluster".
            found = run_job(_config('ws-old', 'ws-new'), 'job-2-cluster')
        assert [record['name'] for record in found] == ['job-2-cluster']

    def test_accessible_workspaces_per_context(self):

        @context.contextual
        def accessible(config: config_utils.Config) -> Set[str]:
            skypilot_config._set_loaded_config(config)
            return workspaces_core.get_accessible_workspace_names(action='read')

        assert accessible(_config('ws-old')) == {'ws-old', 'default'}
        assert accessible(_config('ws-old',
                                  'ws-new')) == {'ws-old', 'ws-new', 'default'}

    def test_concurrent_jobs_each_see_their_own_workspaces(self):
        """Interleaved async jobs, as the controller's event loop runs them.

        Each job loads its config, then repeatedly looks its workspaces up
        from a worker thread while the other job does the same, so the
        cache alternates between the two configs.
        """
        configs = {
            'job-1': _config('ws-1'),
            'job-2': _config('ws-1', 'ws-2'),
            'job-3': _config('ws-3'),
        }

        @context.contextual_async
        async def run_job(job: str) -> List[Set[str]]:
            skypilot_config._set_loaded_config(configs[job])
            seen = []
            for _ in range(20):
                names = await asyncio.to_thread(
                    workspaces_core.get_accessible_workspace_names, 'read')
                seen.append(names)
                await asyncio.sleep(0)
            return seen

        async def main():
            return await asyncio.gather(*(run_job(job) for job in configs))

        results = asyncio.run(main())
        for job, seen in zip(configs, results):
            expected = set(configs[job]['workspaces']) | {'default'}
            assert all(names == expected for names in seen), (job, seen)
