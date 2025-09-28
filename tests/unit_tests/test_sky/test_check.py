"""Unit tests for `sky check` output formatting from sky/check.py."""
import re

import pytest
from click import testing as cli_testing

from sky import clouds as sky_clouds
from sky.clouds.cloud import CloudCapability
import sky.check as sky_check
from sky.client.cli import command


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_summary_message_enabled_infra_with_k8s_contexts(monkeypatch):
    """Validate the summary block formatting produced by sky.check._summary_message."""
    # Prepare enabled clouds and capabilities.
    enabled_clouds = {
        repr(sky_clouds.AWS()): [CloudCapability.COMPUTE, CloudCapability.STORAGE],
        repr(sky_clouds.Azure()): [CloudCapability.COMPUTE, CloudCapability.STORAGE],
        repr(sky_clouds.Kubernetes()): [CloudCapability.COMPUTE],
        repr(sky_clouds.Lambda()): [CloudCapability.COMPUTE],
        repr(sky_clouds.Nebius()): [CloudCapability.COMPUTE],
        repr(sky_clouds.RunPod()): [CloudCapability.COMPUTE],
    }

    # Mock Kubernetes contexts so _format_context_details can render them.
    k8s_context = 'gke_sky-dev-465_us-central1-c_skypilotalpha'
    monkeypatch.setattr(sky_clouds.Kubernetes, 'existing_allowed_contexts',
                        staticmethod(lambda: [k8s_context]))

    # Provide ctx2text for Kubernetes with non-disabled status so it appears in summary.
    cloud2ctx2text = {
        repr(sky_clouds.Kubernetes()): {
            k8s_context: 'enabled. Note: Cluster has 1 nodes with accelerators that are not labeled.'
        }
    }

    # Render the summary (hide workspace name; no disallowed cloud hint).
    summary = sky_check._summary_message(enabled_clouds, cloud2ctx2text,
                                         current_workspace_name='default',
                                         hide_workspace_str=True,
                                         disallowed_cloud_names=[])
    summary_plain = strip_ansi(summary)

    # Check key lines and ordering.
    assert 'Enabled infra' in summary_plain

    expected_lines = [
        'AWS [compute, storage]',
        'Azure [compute, storage]',
        'Kubernetes [compute]',
        'Allowed contexts:',
        f'└── {k8s_context}',
        'Lambda [compute]',
        'Nebius [compute]',
        'RunPod [compute]',
    ]
    for line in expected_lines:
        assert line in summary_plain

    # Ensure lexicographic ordering of enabled clouds in the summary.
    order = [
        'AWS [compute, storage]',
        'Azure [compute, storage]',
        'Kubernetes [compute]',
        'Lambda [compute]',
        'Nebius [compute]',
        'RunPod [compute]',
    ]
    positions = [summary_plain.index(x) for x in order]
    assert positions == sorted(positions)


def test_k8s_summary_allowed_contexts_default_config(monkeypatch):
    """Multiple contexts honoring default (global) allowed_contexts config."""
    # Kube contexts present on the system
    all_contexts = ['ctx-a', 'ctx-b', 'ctx-c']
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_all_kube_context_names',
        lambda: list(all_contexts))

    # No workspace-specific allowed_contexts; fall back to global default
    monkeypatch.setattr('sky.skypilot_config.get_workspace_cloud',
                        lambda *args, **kwargs: {})
    monkeypatch.setattr('sky.skypilot_config.get_effective_region_config',
                        lambda **kwargs: ['ctx-b', 'ctx-c'])

    # ctx2text marks only allowed contexts as enabled for summary inclusion
    ctx2text = {
        'ctx-b': 'enabled.',
        'ctx-c': 'enabled.',
        # ctx-a omitted -> not shown in summary
    }

    enabled_clouds = {
        repr(sky_clouds.Kubernetes()): [CloudCapability.COMPUTE],
    }

    summary = sky_check._summary_message(enabled_clouds, {repr(sky_clouds.Kubernetes()): ctx2text},
                                         current_workspace_name='default',
                                         hide_workspace_str=True,
                                         disallowed_cloud_names=[])
    s = strip_ansi(summary)

    assert 'Kubernetes [compute]' in s
    # Only ctx-b and ctx-c appear, in the same order as allowed_contexts
    assert '├── ctx-b' in s
    assert '└── ctx-c' in s
    # Ensure ctx-a is not listed
    assert 'ctx-a' not in s


def test_k8s_summary_allowed_contexts_workspace_override(monkeypatch):
    """Workspace allowed_contexts overrides default config."""
    # Kube contexts present on the system
    all_contexts = ['ctx-a', 'ctx-b', 'ctx-c']
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_all_kube_context_names',
        lambda: list(all_contexts))

    # Global default allows a and b
    monkeypatch.setattr('sky.skypilot_config.get_effective_region_config',
                        lambda **kwargs: ['ctx-a', 'ctx-b'])
    # Workspace-specific allows only c
    def mock_get_workspace_cloud(cloud: str, workspace=None):
        if workspace is None:
            # existing_allowed_contexts reads active workspace implicitly, not via arg
            # so we return based on the active workspace, which tests set via context
            pass
        # Return a dict-like object
        return {'allowed_contexts': ['ctx-c']} if sky_check.skypilot_config.get_active_workspace() == 'ws1' else {}

    monkeypatch.setattr('sky.skypilot_config.get_workspace_cloud',
                        mock_get_workspace_cloud)

    enabled_clouds = {
        repr(sky_clouds.Kubernetes()): [CloudCapability.COMPUTE],
    }

    # Provide ctx2text for all contexts; only allowed ones (per workspace) will show
    ctx2text_all = {'ctx-a': 'enabled.', 'ctx-b': 'enabled.', 'ctx-c': 'enabled.'}

    # In default workspace: should show a, b (global default)
    with sky_check.skypilot_config.local_active_workspace_ctx('default'):
        summary_default = sky_check._summary_message(
            enabled_clouds, {repr(sky_clouds.Kubernetes()): ctx2text_all},
            current_workspace_name='default',
            hide_workspace_str=True,
            disallowed_cloud_names=[])
        s_default = strip_ansi(summary_default)
        assert '├── ctx-a' in s_default or '└── ctx-a' in s_default
        assert 'ctx-b' in s_default
        assert 'ctx-c' not in s_default

    # In ws1 workspace: should show only c (workspace override)
    with sky_check.skypilot_config.local_active_workspace_ctx('ws1'):
        summary_ws1 = sky_check._summary_message(
            enabled_clouds, {repr(sky_clouds.Kubernetes()): ctx2text_all},
            current_workspace_name='ws1',
            hide_workspace_str=True,
            disallowed_cloud_names=[])
        s_ws1 = strip_ansi(summary_ws1)
        assert 'ctx-c' in s_ws1
        assert 'ctx-a' not in s_ws1
        assert 'ctx-b' not in s_ws1

def test_cli_check_prints_server_url(monkeypatch):
    """`sky check` should print the API server URL at the end (smoke check)."""
    # Mock the SDK call chain used by the CLI entrypoint.
    monkeypatch.setattr('sky.client.sdk.check', lambda *args, **kwargs: 'req-1')
    monkeypatch.setattr('sky.client.sdk.stream_and_get', lambda *args, **kwargs: None)

    # Mock the server URL to a deterministic value.
    server_url = 'http://localhost:12345'
    monkeypatch.setattr('sky.server.common.get_server_url', lambda: server_url)

    runner = cli_testing.CliRunner()
    result = runner.invoke(command.check, [])

    # Command should complete successfully and include the server URL line.
    assert result.exit_code == 0
    assert f'Using SkyPilot API server: {server_url}' in result.stdout


def _mock_k8s_env(monkeypatch,
                  all_contexts,
                  workspace_allowed_contexts=None,
                  global_allowed_contexts=None,
                  check_note=None):
    """Helper to mock kubernetes utils and config for tests.

    - all_contexts: list of contexts returned by get_all_kube_context_names
    - workspace_allowed_contexts: dict workspace -> list[str] for get_workspace_cloud
    - global_allowed_contexts: list[str] for get_effective_region_config
    - check_note: optional note appended by check_credentials for enabled ctxs
    """
    # Prevent dependency checks from failing
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.check_port_forward_mode_dependencies',
        lambda *_args, **_kwargs: None)
    # Kube env
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_all_kube_context_names',
        lambda: list(all_contexts))
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_current_kube_config_context_name',
        lambda: all_contexts[0] if all_contexts else None)
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.is_incluster_config_available',
        lambda: False)

    # Mock per-context credential checks as enabled
    def mock_check_credentials(context, run_optional_checks=True):
        del run_optional_checks
        return True, check_note

    monkeypatch.setattr('sky.provision.kubernetes.utils.check_credentials',
                        mock_check_credentials)
    # Avoid reading real kube identities
    monkeypatch.setattr(
        'sky.clouds.kubernetes.Kubernetes.get_active_user_identity_str',
        classmethod(lambda cls: 'mocked-k8s-identity'))

    # Config: allowed clouds and allowed contexts
    monkeypatch.setattr('sky.skypilot_config.get_nested',
                        lambda keys, default_value=None: ['Kubernetes']
                        if keys == ('allowed_clouds',) else default_value)

    def mock_get_workspace_cloud(cloud: str, workspace: str = None):
        del cloud
        ws = workspace or sky_check.skypilot_config.get_active_workspace()
        allowed = None
        if workspace_allowed_contexts is not None:
            allowed = workspace_allowed_contexts.get(ws)
        return {'allowed_contexts': allowed} if allowed is not None else {}

    monkeypatch.setattr('sky.skypilot_config.get_workspace_cloud',
                        mock_get_workspace_cloud)
    if global_allowed_contexts is not None:
        monkeypatch.setattr('sky.skypilot_config.get_effective_region_config',
                            lambda **kwargs: list(global_allowed_contexts))

    # Workspaces
    monkeypatch.setattr('sky.workspaces.core.get_workspaces',
                        lambda: {'default': {}, 'ws1': {}})

    # Avoid touching real user state
    monkeypatch.setattr('sky.global_user_state.get_cached_enabled_clouds',
                        lambda *args, **kwargs: [])
    monkeypatch.setattr('sky.global_user_state.set_enabled_clouds',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr('sky.global_user_state.set_allowed_clouds',
                        lambda *args, **kwargs: None)


def test_check_capabilities_k8s_default_allowed_contexts(monkeypatch, capsys):
    """check_capabilities lists only globally-allowed Kubernetes contexts."""
    _mock_k8s_env(
        monkeypatch,
        all_contexts=['ctx-a', 'ctx-b', 'ctx-c'],
        workspace_allowed_contexts=None,  # use global
        global_allowed_contexts=['ctx-b', 'ctx-c'],
        check_note=None,
    )

    sky_check.check_capabilities(
        quiet=False,
        verbose=True,
        clouds=('kubernetes',),
        capabilities=[CloudCapability.COMPUTE],
        workspace=None,
    )
    out = strip_ansi(capsys.readouterr().out)
    # Should show only ctx-b and ctx-c under Kubernetes summary
    assert 'Kubernetes [compute]' in out
    assert '├── ctx-b' in out
    assert '└── ctx-c' in out
    assert 'ctx-a' not in out


def test_check_capabilities_k8s_workspace_override(monkeypatch, capsys):
    """Workspace allowed_contexts override global config in check_capabilities."""
    _mock_k8s_env(
        monkeypatch,
        all_contexts=['ctx-a', 'ctx-b', 'ctx-c'],
        workspace_allowed_contexts={
            'default': ['ctx-a', 'ctx-b'],
            'ws1': ['ctx-c'],
        },
        global_allowed_contexts=['ctx-a'],  # ignored when workspace override
        check_note=None,
    )

    # Run across all workspaces
    sky_check.check_capabilities(
        quiet=False,
        verbose=True,
        clouds=('kubernetes',),
        capabilities=[CloudCapability.COMPUTE],
        workspace=None,
    )
    out = strip_ansi(capsys.readouterr().out)

    # default workspace section should include ctx-a and ctx-b only
    assert "Enabled infra for workspace: 'default'" in out
    start = out.index("Enabled infra for workspace: 'default'")
    # Bound the section to before the next workspace's "Checking" header
    try:
        end = out.index("Checking enabled infra for workspace: 'ws1'", start)
    except ValueError:
        end = len(out)
    default_section = out[start:end]
    assert 'Kubernetes [compute]' in default_section
    assert 'ctx-a' in default_section
    assert 'ctx-b' in default_section
    assert 'ctx-c' not in default_section

    # ws1 workspace section should include ctx-c only
    assert "Enabled infra for workspace: 'ws1'" in out
    start = out.index("Enabled infra for workspace: 'ws1'")
    # Bound to end of string
    end = len(out)
    ws1_section = out[start:end]
    assert 'Kubernetes [compute]' in ws1_section
    assert 'ctx-c' in ws1_section
    assert 'ctx-a' not in ws1_section
    assert 'ctx-b' not in ws1_section
