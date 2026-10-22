"""Verify sky.check.check persists cloud2ctx2text into check_results."""
import time

import pytest

from sky import clouds as sky_clouds
from sky import dag as dag_lib
from sky import exceptions
from sky import global_user_state
from sky import models
from sky import optimizer
from sky import resources as resources_lib
from sky import skypilot_config
from sky import task as task_lib
import sky.check as sky_check
from sky.clouds.cloud import CloudCapability
from sky.skylet import constants
from sky.utils import annotations
from sky.utils.db import db_utils


def _fresh_db(tmp_path, monkeypatch):
    """Mirror the fixture used in test_global_user_state_check_results.py."""
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.setattr(
        global_user_state,
        '_db_manager',
        db_utils.DatabaseManager(
            'state',
            global_user_state.create_table,
            post_init_fn=lambda _: global_user_state._sqlite_supports_returning(
            ),
        ),
    )


def _common_mocks(monkeypatch):
    """Mocks shared across the persist tests.

    Constrains the registry / config / workspace listing so check_capabilities
    only iterates over the clouds we want, then lets the real inner closures
    build cloud2ctx2text + check_results_dict from canned check results.
    """
    # Config: allowed_clouds returns whatever default get_all_clouds passes
    # in (there's no allowed_clouds key in the fake config).
    monkeypatch.setattr('sky.skypilot_config.get_nested',
                        lambda keys, default_value=None: default_value)
    monkeypatch.setattr('sky.skypilot_config.get_workspace_cloud',
                        lambda *args, **kwargs: {})

    # Workspaces: the caller can write 'default' and only read 'ws-ro'. Every
    # workspace the check loop visits gets its cached enabled-clouds and
    # check-results rows rewritten, so it must visit the writable set only,
    # never the wider read-only-visible one.
    monkeypatch.setattr('sky.workspaces.core.get_workspaces', lambda: {
        'default': {},
        'ws-ro': {}
    })
    monkeypatch.setattr('sky.workspaces.core.get_workspace_access_sets', lambda:
                        ({'default', 'ws-ro'}, {'default'}))
    monkeypatch.setattr('sky.utils.common_utils.get_current_user',
                        lambda: models.User(id='bob1234', name='bob'))

    # No previously enabled clouds.
    monkeypatch.setattr('sky.global_user_state.get_cached_enabled_clouds',
                        lambda *args, **kwargs: [])
    # set_enabled_clouds and set_allowed_clouds will write into the fresh DB,
    # which is harmless for these tests.


def _restrict_registry(monkeypatch, cloud_names):
    """Limit registry.CLOUD_REGISTRY iteration to specific clouds."""
    from sky.utils import registry
    full = dict(registry.CLOUD_REGISTRY)
    restricted = {k: v for k, v in full.items() if k in cloud_names}

    # Patch the registry's items used by sky/check.py: .values() (in
    # get_all_clouds) and .from_str (in get_cloud_tuple). The simplest hack is
    # to monkeypatch the dict's values() and from_str.
    monkeypatch.setattr(registry.CLOUD_REGISTRY, 'values', restricted.values)

    original_from_str = registry.CLOUD_REGISTRY.from_str

    def patched_from_str(name):
        if name is not None and name.lower() in restricted:
            return restricted[name.lower()]
        return original_from_str(name)

    monkeypatch.setattr(registry.CLOUD_REGISTRY, 'from_str', patched_from_str)


def test_persists_dict_reasons_and_string_reasons(tmp_path, monkeypatch):
    """Both k8s (dict reason) and AWS (string reason) get stored, ANSI-stripped."""
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws', 'kubernetes'})

    # combinations are built in registry-iteration order; AWS comes before
    # Kubernetes in the registry. The single-capability case yields exactly
    # one entry per cloud.
    fake_results = [
        # AWS -> string reason (with ANSI codes)
        (CloudCapability.COMPUTE, True, '\x1b[32menabled.\x1b[0m'),
        # Kubernetes -> dict reason (per-context)
        (CloudCapability.COMPUTE, True, {
            'ctx-a': '\x1b[32menabled.\x1b[0m',
            'ctx-b': '\x1b[31mdisabled. Forbidden\x1b[0m',
        }),
    ]
    monkeypatch.setattr('sky.utils.subprocess_utils.run_in_parallel',
                        lambda *args, **kwargs: fake_results)
    # Avoid pretty-printing branches that consult cloud-specific helpers.
    monkeypatch.setattr(sky_check, '_print_checked_cloud',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(sky_check, '_summary_message',
                        lambda *args, **kwargs: '')

    sky_check.check_capabilities(quiet=True,
                                 verbose=False,
                                 clouds=None,
                                 capabilities=[CloudCapability.COMPUTE],
                                 workspace='default')

    persisted = global_user_state.get_cached_check_results('default')
    assert persisted == {
        repr(sky_clouds.AWS()): {
            '': {
                'enabled': True,
                'reason': 'enabled.'
            }
        },
        repr(sky_clouds.Kubernetes()): {
            'ctx-a': {
                'enabled': True,
                'reason': 'enabled.'
            },
            'ctx-b': {
                'enabled': False,
                'reason': 'disabled. Forbidden'
            },
        },
    }


def test_full_workspace_run_drops_disallowed_clouds(tmp_path, monkeypatch):
    """A subsequent full run that no longer covers GCP must drop it."""
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.set_check_results(
        {
            repr(sky_clouds.AWS()): {
                '': {
                    'enabled': True,
                    'reason': 'enabled.'
                }
            },
            repr(sky_clouds.GCP()): {
                '': {
                    'enabled': True,
                    'reason': 'enabled.'
                }
            },
        },
        workspace='default',
        is_full_workspace_run=True,
    )

    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws'})

    fake_results = [(CloudCapability.COMPUTE, True, 'enabled.')]
    monkeypatch.setattr('sky.utils.subprocess_utils.run_in_parallel',
                        lambda *args, **kwargs: fake_results)
    monkeypatch.setattr(sky_check, '_print_checked_cloud',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(sky_check, '_summary_message',
                        lambda *args, **kwargs: '')

    sky_check.check_capabilities(quiet=True,
                                 verbose=False,
                                 clouds=None,
                                 capabilities=[CloudCapability.COMPUTE],
                                 workspace='default')

    persisted = global_user_state.get_cached_check_results('default')
    assert repr(sky_clouds.GCP()) not in persisted
    assert persisted[repr(sky_clouds.AWS())] == {
        '': {
            'enabled': True,
            'reason': 'enabled.'
        }
    }


def test_scoped_run_preserves_other_clouds(tmp_path, monkeypatch):
    """clouds=('aws',) must NOT erase Kubernetes leaves."""
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.set_check_results(
        {
            repr(sky_clouds.AWS()): {
                '': {
                    'enabled': True,
                    'reason': 'enabled.'
                }
            },
            repr(sky_clouds.Kubernetes()): {
                'ctx-a': {
                    'enabled': True,
                    'reason': 'enabled.'
                }
            },
        },
        workspace='default',
        is_full_workspace_run=True,
    )

    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws'})

    fake_results = [(CloudCapability.COMPUTE, False, 'creds expired')]
    monkeypatch.setattr('sky.utils.subprocess_utils.run_in_parallel',
                        lambda *args, **kwargs: fake_results)
    monkeypatch.setattr(sky_check, '_print_checked_cloud',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(sky_check, '_summary_message',
                        lambda *args, **kwargs: '')

    sky_check.check_capabilities(quiet=True,
                                 verbose=False,
                                 clouds=('aws',),
                                 capabilities=[CloudCapability.COMPUTE],
                                 workspace='default')

    persisted = global_user_state.get_cached_check_results('default')
    assert persisted == {
        repr(sky_clouds.AWS()): {
            '': {
                'enabled': False,
                'reason': 'creds expired'
            }
        },
        repr(sky_clouds.Kubernetes()): {
            'ctx-a': {
                'enabled': True,
                'reason': 'enabled.'
            }
        },
    }


def test_strip_ansi_helper():
    """Sanity: _strip_ansi removes color codes."""
    assert sky_check._strip_ansi('\x1b[32mfoo\x1b[0m') == 'foo'
    assert sky_check._strip_ansi('plain') == 'plain'
    assert sky_check._strip_ansi(None) == ''


def test_multi_capability_aws_uses_failure_reason(tmp_path, monkeypatch):
    """If AWS has COMPUTE ok=True but STORAGE ok=False, the persisted
    leaf is enabled=False with the failure reason."""
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws'})

    # Two capabilities for AWS -> two combinations -> two fake results.
    fake_results = [
        (CloudCapability.COMPUTE, True, 'enabled.'),
        (CloudCapability.STORAGE, False, 'storage role missing'),
    ]
    monkeypatch.setattr('sky.utils.subprocess_utils.run_in_parallel',
                        lambda *args, **kwargs: fake_results)
    monkeypatch.setattr(sky_check, '_print_checked_cloud',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(sky_check, '_summary_message',
                        lambda *args, **kwargs: '')

    sky_check.check_capabilities(
        quiet=True,
        verbose=False,
        clouds=None,
        capabilities=[CloudCapability.COMPUTE, CloudCapability.STORAGE],
        workspace='default')

    persisted = global_user_state.get_cached_check_results('default')
    assert persisted == {
        repr(sky_clouds.AWS()): {
            '': {
                'enabled': False,
                'reason': 'storage role missing'
            }
        },
    }


def test_k8s_exception_reason_marked_disabled(tmp_path, monkeypatch):
    """An exception message (no 'enabled.' prefix, no 'disabled.' prefix)
    must be marked enabled=False, not enabled=True."""
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'kubernetes'})

    # Kubernetes -> dict reason, but the per-context text is an exception
    # message that doesn't start with 'enabled.' or 'disabled.'.
    fake_results = [
        (CloudCapability.COMPUTE, False, {
            'ctx-broken': 'Forbidden: User cannot list nodes in cluster',
        }),
    ]
    monkeypatch.setattr('sky.utils.subprocess_utils.run_in_parallel',
                        lambda *args, **kwargs: fake_results)
    monkeypatch.setattr(sky_check, '_print_checked_cloud',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(sky_check, '_summary_message',
                        lambda *args, **kwargs: '')

    sky_check.check_capabilities(quiet=True,
                                 verbose=False,
                                 clouds=None,
                                 capabilities=[CloudCapability.COMPUTE],
                                 workspace='default')

    persisted = global_user_state.get_cached_check_results('default')
    kube_repr = repr(sky_clouds.Kubernetes())
    assert persisted[kube_repr]['ctx-broken']['enabled'] is False
    assert 'Forbidden' in persisted[kube_repr]['ctx-broken']['reason']


def _aws_enabled(monkeypatch):
    """Every check run finds AWS enabled, without touching the network."""
    _restrict_registry(monkeypatch, {'aws'})
    monkeypatch.setattr(
        'sky.utils.subprocess_utils.run_in_parallel',
        lambda *args, **kwargs: [(CloudCapability.COMPUTE, True, 'enabled.')])
    monkeypatch.setattr(sky_check, '_print_checked_cloud',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(sky_check, '_summary_message',
                        lambda *args, **kwargs: '')


def _record_enabled_cloud_writes(monkeypatch):
    writes = []
    monkeypatch.setattr(
        'sky.global_user_state.set_enabled_clouds',
        lambda clouds, capability, workspace: writes.append(workspace))
    return writes


def test_full_run_visits_only_writable_workspaces(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _aws_enabled(monkeypatch)
    writes = _record_enabled_cloud_writes(monkeypatch)

    result = sky_check.check_capabilities(
        quiet=True, capabilities=[CloudCapability.COMPUTE])

    assert set(result) == {'default'}
    assert writes == ['default']
    assert global_user_state.get_cached_check_results('ws-ro') == {}


def test_readable_workspace_is_checked_without_persisting(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _aws_enabled(monkeypatch)
    writes = _record_enabled_cloud_writes(monkeypatch)

    result = sky_check.check_capabilities(
        quiet=True,
        capabilities=[CloudCapability.COMPUTE],
        workspace='ws-ro',
        persist=False)

    assert result == {
        'ws-ro': {
            repr(sky_clouds.AWS()): [CloudCapability.COMPUTE]
        }
    }
    assert not writes
    assert global_user_state.get_cached_check_results('ws-ro') == {}
    assert global_user_state.get_allowed_clouds('ws-ro') == []


def test_readable_workspace_cannot_be_persisted(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _aws_enabled(monkeypatch)

    with pytest.raises(ValueError) as e:
        sky_check.check_capabilities(quiet=True,
                                     capabilities=[CloudCapability.COMPUTE],
                                     workspace='ws-ro')

    assert str(e.value) == (
        'Workspace \'ws-ro\' is read-only for user \'bob\' (bob1234); '
        'checking its infra needs write access. Workspaces this user can '
        'write: default.')


@pytest.mark.parametrize('writable,hint', [
    ({'default'}, 'Workspaces this user can write: default.'),
    (set(), 'This user cannot write to any workspace.'),
])
def test_unreadable_workspace_does_not_reveal_whether_it_exists(
        tmp_path, monkeypatch, writable, hint):
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    monkeypatch.setattr('sky.workspaces.core.get_workspace_access_sets', lambda:
                        (writable | {'ws-ro'}, writable))

    # Same message for a private workspace and for one that does not exist.
    for workspace in ('ws-private', 'ws-missing'):
        for persist in (True, False):
            with pytest.raises(ValueError) as e:
                sky_check.check_capabilities(
                    quiet=True,
                    capabilities=[CloudCapability.COMPUTE],
                    workspace=workspace,
                    persist=persist)
            assert str(e.value) == (
                f'Workspace {workspace!r} is not found or not accessible to '
                f'user \'bob\' (bob1234). {hint}')


def test_refresh_for_a_reader_returns_fresh_clouds_without_persisting(
        tmp_path, monkeypatch):
    """A cold cache on a workspace the caller can only read does not fail."""
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _aws_enabled(monkeypatch)
    writes = _record_enabled_cloud_writes(monkeypatch)

    with skypilot_config.local_active_workspace_ctx('ws-ro'):
        enabled = sky_check.get_cached_enabled_clouds_or_refresh(
            CloudCapability.COMPUTE, raise_if_no_cloud_access=True)

    assert [repr(c) for c in enabled] == [repr(sky_clouds.AWS())]
    assert not writes
    assert global_user_state.get_cached_check_results('ws-ro') == {}
    assert global_user_state.get_allowed_clouds('ws-ro') == []


def test_refresh_for_a_writer_still_persists(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _aws_enabled(monkeypatch)
    writes = _record_enabled_cloud_writes(monkeypatch)

    with skypilot_config.local_active_workspace_ctx('default'):
        sky_check.get_cached_enabled_clouds_or_refresh(CloudCapability.COMPUTE)

    assert writes == ['default']
    assert global_user_state.get_cached_check_results('default') != {}


def _probes(monkeypatch, enabled):
    """Stands in for the credential probes: clouds in `enabled` pass.

    Returns the list of probe runs, one entry (cloud names) per run.
    """
    runs = []

    def _run(func, combinations, *args, **kwargs):
        del func, args, kwargs
        runs.append([cloud_tuple[0] for cloud_tuple, _, _ in combinations])
        return [(capability, cloud_tuple[0] in enabled, 'checked')
                for cloud_tuple, capability, _ in combinations]

    monkeypatch.setattr('sky.utils.subprocess_utils.run_in_parallel', _run)
    monkeypatch.setattr(sky_check, '_print_checked_cloud',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(sky_check, '_summary_message',
                        lambda *args, **kwargs: '')
    # Start from empty probe caches.
    annotations.clear_request_level_cache()
    # pylint: disable-next=protected-access
    sky_check._unstored_enabled_cloud_names.cache_clear()
    return runs


def test_reader_probe_is_shared_per_config_until_it_expires(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws'})
    runs = _probes(monkeypatch, {repr(sky_clouds.AWS())})

    with skypilot_config.local_active_workspace_ctx('ws-ro'):
        for _ in range(3):
            sky_check.get_cached_enabled_clouds_or_refresh(
                CloudCapability.COMPUTE)
        assert len(runs) == 1
        # Nor does a request whose config would change the probe.
        monkeypatch.setattr(
            'sky.skypilot_config.to_dict',
            lambda: {'kubernetes': {
                'allowed_contexts': ['other']
            }})
        sky_check.get_cached_enabled_clouds_or_refresh(CloudCapability.COMPUTE)
        assert len(runs) == 2
        # Another reader with the same config shares the probe.
        monkeypatch.setattr('sky.utils.common_utils.get_current_user',
                            lambda: models.User(id='carol123', name='carol'))
        sky_check.get_cached_enabled_clouds_or_refresh(CloudCapability.COMPUTE)
        assert len(runs) == 2
        # A caller who cannot read the workspace never gets the shared answer.
        monkeypatch.setattr('sky.utils.common_utils.get_current_user',
                            lambda: models.User(id='dave1234', name='dave'))
        monkeypatch.setattr('sky.workspaces.core.get_workspace_access_sets',
                            lambda: ({'default'}, {'default'}))
        with pytest.raises(ValueError, match='not found or not accessible'):
            sky_check.get_cached_enabled_clouds_or_refresh(
                CloudCapability.COMPUTE)
        assert len(runs) == 2
        # A later request shares it too, until it expires.
        monkeypatch.setattr('sky.workspaces.core.get_workspace_access_sets',
                            lambda: ({'default', 'ws-ro'}, {'default'}))
        annotations.clear_request_level_cache()
        sky_check.get_cached_enabled_clouds_or_refresh(CloudCapability.COMPUTE)
        assert len(runs) == 2
        _age_request_caches(61)
        sky_check.get_cached_enabled_clouds_or_refresh(CloudCapability.COMPUTE)
        assert len(runs) == 3


def test_recheck_for_a_reader_returns_without_persisting(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws', 'gcp'})
    _probes(monkeypatch, {repr(sky_clouds.GCP())})
    writes = _record_enabled_cloud_writes(monkeypatch)

    with skypilot_config.local_active_workspace_ctx('ws-ro'):
        enabled = sky_check.recheck_clouds(CloudCapability.COMPUTE, ['GCP'])

    assert enabled == {repr(sky_clouds.GCP())}
    assert not writes
    assert global_user_state.get_cached_check_results('ws-ro') == {}
    assert global_user_state.get_allowed_clouds('ws-ro') == []


def test_recheck_for_a_writer_still_persists(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws', 'gcp'})
    _probes(monkeypatch, {repr(sky_clouds.GCP())})
    writes = _record_enabled_cloud_writes(monkeypatch)

    with skypilot_config.local_active_workspace_ctx('default'):
        enabled = sky_check.recheck_clouds(CloudCapability.COMPUTE, ['GCP'])

    assert enabled == {repr(sky_clouds.GCP())}
    assert writes == ['default']


@pytest.mark.parametrize('enabled,error', [
    ({'AWS'}, 'requires gcp which is not enabled'),
    ({'AWS', 'GCP'}, None),
])
def test_pinned_cloud_for_a_reader_is_judged_on_the_fresh_answer(
        tmp_path, monkeypatch, enabled, error):
    """A task pinned to a cloud gets "not enabled", not a write-access error."""
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws', 'gcp'})
    _probes(monkeypatch, enabled)
    task = task_lib.Task(run='echo hi')
    task.set_resources(resources_lib.Resources(infra='gcp'))
    with dag_lib.Dag() as dag:
        dag.add(task)

    with skypilot_config.local_active_workspace_ctx('ws-ro'):
        if error is None:
            # pylint: disable-next=protected-access
            optimizer._check_specified_clouds(dag)
        else:
            with pytest.raises(exceptions.ResourcesUnavailableError) as e:
                # pylint: disable-next=protected-access
                optimizer._check_specified_clouds(dag)
            assert error in str(e.value).lower()


def _age_request_caches(seconds):
    """Moves the probe caches forward, as a long request would."""
    # pylint: disable=protected-access
    for func in (sky_check._unstored_enabled_cloud_names,
                 sky_check._reader_rechecked):
        # pylint: enable=protected-access
        cache = getattr(func, 'cache', None)
        if cache is not None:
            cache.expire(time.monotonic() + seconds)


@pytest.mark.parametrize('elapsed', [0, 120])
def test_reader_recheck_is_seen_by_later_reads_in_the_request(
        tmp_path, monkeypatch, elapsed):
    """A writer's warm cache lists AWS only; GCP has since become usable.

    The reader's re-check finds GCP and is not stored, yet later reads in the
    same request (the optimizer's feasibility pass) must still see it, even
    when the optimizer runs for minutes in between.
    """
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws', 'gcp'})
    _probes(monkeypatch, {repr(sky_clouds.AWS()), repr(sky_clouds.GCP())})
    monkeypatch.setattr('sky.global_user_state.get_cached_enabled_clouds',
                        lambda *args, **kwargs: [sky_clouds.AWS()])
    monkeypatch.setattr(
        'sky.global_user_state.get_allowed_clouds',
        # pylint: disable-next=protected-access
        sky_check._get_workspace_allowed_clouds)
    writes = _record_enabled_cloud_writes(monkeypatch)
    task = task_lib.Task(run='echo hi')
    task.set_resources(resources_lib.Resources(infra='gcp'))
    with dag_lib.Dag() as dag:
        dag.add(task)

    with skypilot_config.local_active_workspace_ctx('ws-ro'):
        # pylint: disable-next=protected-access
        optimizer._check_specified_clouds(dag)
        _age_request_caches(elapsed)
        enabled = sky_check.get_cached_enabled_clouds_or_refresh(
            CloudCapability.COMPUTE)

    assert {repr(c) for c in enabled} == {'AWS', 'GCP'}
    assert not writes


def test_reader_recheck_is_kept_per_config(tmp_path, monkeypatch):
    """A re-check made under one config is not seen under another."""
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws', 'gcp'})
    _probes(monkeypatch, {repr(sky_clouds.AWS()), repr(sky_clouds.GCP())})
    monkeypatch.setattr('sky.global_user_state.get_cached_enabled_clouds',
                        lambda *args, **kwargs: [sky_clouds.AWS()])
    monkeypatch.setattr(
        'sky.global_user_state.get_allowed_clouds',
        # pylint: disable-next=protected-access
        sky_check._get_workspace_allowed_clouds)
    config = {'kubernetes': {'allowed_contexts': ['a']}}
    monkeypatch.setattr('sky.skypilot_config.to_dict', lambda: config)

    def enabled():
        return {
            repr(c) for c in sky_check.get_cached_enabled_clouds_or_refresh(
                CloudCapability.COMPUTE)
        }

    with skypilot_config.local_active_workspace_ctx('ws-ro'):
        sky_check.recheck_clouds(CloudCapability.COMPUTE, ['GCP'])
        assert enabled() == {'AWS', 'GCP'}
        config = {'kubernetes': {'allowed_contexts': ['b']}}
        assert enabled() == {'AWS'}


def test_writer_reads_add_no_reader_recheck_entries(tmp_path, monkeypatch):
    """Only re-checks add entries, so a writer's reads cannot crowd them out."""
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws'})
    _probes(monkeypatch, {repr(sky_clouds.AWS())})

    for _ in range(3):
        sky_check.get_cached_enabled_clouds_or_refresh(CloudCapability.COMPUTE)

    # pylint: disable-next=protected-access
    assert sky_check._reader_rechecked() == {}


def test_config_fingerprint_accepts_keys_of_mixed_types(monkeypatch):
    """A valid config may key a mapping by both strings and numbers."""
    labels = {'team': 'ml', 8080: 'web'}
    monkeypatch.setattr(
        'sky.skypilot_config.to_dict', lambda:
        {'kubernetes': {
            'pod_config': {
                'metadata': {
                    'labels': labels
                }
            }
        }})
    # pylint: disable-next=protected-access
    first = sky_check._config_fingerprint()
    # pylint: disable-next=protected-access
    assert sky_check._config_fingerprint() == first
    labels[8080] = 'api'
    # pylint: disable-next=protected-access
    assert sky_check._config_fingerprint() != first


def test_reader_with_a_warm_cache_does_not_probe(tmp_path, monkeypatch):
    """A stored answer from a writer is served to a reader as is."""
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws', 'gcp'})
    runs = _probes(monkeypatch, {repr(sky_clouds.GCP())})
    monkeypatch.setattr('sky.global_user_state.get_cached_enabled_clouds',
                        lambda *args, **kwargs: [sky_clouds.AWS()])
    monkeypatch.setattr(
        'sky.global_user_state.get_allowed_clouds',
        # pylint: disable-next=protected-access
        sky_check._get_workspace_allowed_clouds)

    with skypilot_config.local_active_workspace_ctx('ws-ro'):
        enabled = sky_check.get_cached_enabled_clouds_or_refresh(
            CloudCapability.COMPUTE)

    assert [repr(c) for c in enabled] == ['AWS']
    assert not runs


@pytest.mark.parametrize('workspace,hint', [
    ('default', 'Run: '),
    ('ws-ro', 'Ask a member of workspace \'ws-ro\' or an admin to run: '),
])
def test_no_cloud_hint_matches_who_can_refresh(tmp_path, monkeypatch, workspace,
                                               hint):
    """Only a writer's own `sky check` refreshes the workspace."""
    _fresh_db(tmp_path, monkeypatch)
    _common_mocks(monkeypatch)
    _restrict_registry(monkeypatch, {'aws'})
    _probes(monkeypatch, set())

    with skypilot_config.local_active_workspace_ctx(workspace):
        with pytest.raises(exceptions.NoCloudAccessError) as e:
            sky_check.get_cached_enabled_clouds_or_refresh(
                CloudCapability.COMPUTE, raise_if_no_cloud_access=True)

    assert f'Cloud access is not set up. {hint}' in str(e.value)
