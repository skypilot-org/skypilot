"""Verify sky.check.check persists cloud2ctx2text into check_results."""
from sky import clouds as sky_clouds
from sky import global_user_state
import sky.check as sky_check
from sky.clouds.cloud import CloudCapability
from sky.skylet import constants
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

    # Workspaces: only 'default'.
    monkeypatch.setattr('sky.workspaces.core.get_workspaces',
                        lambda: {'default': {}})
    monkeypatch.setattr('sky.workspaces.core.get_accessible_workspace_names',
                        lambda: ['default'])

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
