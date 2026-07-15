"""Tests for workspace config deep-merge behavior."""
import copy
from unittest import mock

import pytest

from sky import skypilot_config
from sky.utils import config_utils


def _make_config(config_dict):
    """Create a Config from a dict, mocking the loaded config."""
    return config_utils.Config(config_dict)


class TestGetWorkspaceCloudDeepMerge:
    """Test that get_workspace_cloud deep-merges global + workspace config."""

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_workspace_overrides_global_field(self, mock_config,
                                              mock_workspace):
        """Workspace-specific field overrides global."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({
            'kubernetes': {
                'namespace': 'global-ns',
                'allowed_contexts': ['ctx-a', 'ctx-b'],
            },
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'namespace': 'team-a-ns',
                    }
                }
            }
        })

        result = skypilot_config.get_workspace_cloud('kubernetes')
        assert result['namespace'] == 'team-a-ns'
        assert result['allowed_contexts'] == ['ctx-a', 'ctx-b']

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_workspace_inherits_all_global_fields(self, mock_config,
                                                  mock_workspace):
        """Empty workspace block inherits everything from global."""
        mock_workspace.return_value = 'team-b'
        mock_config.return_value = _make_config({
            'gcp': {
                'project_id': 'global-project',
                'disabled': False,
            },
            'workspaces': {
                'team-b': {
                    'gcp': {}
                }
            }
        })

        result = skypilot_config.get_workspace_cloud('gcp')
        assert result['project_id'] == 'global-project'
        assert result['disabled'] is False

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_no_workspace_block_returns_global(self, mock_config,
                                               mock_workspace):
        """No workspace defined — returns global config unchanged."""
        mock_workspace.return_value = 'nonexistent'
        mock_config.return_value = _make_config({
            'gcp': {
                'project_id': 'global-project',
                'disabled': False,
            },
        })

        result = skypilot_config.get_workspace_cloud('gcp')
        assert result['project_id'] == 'global-project'
        assert result['disabled'] is False

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_no_global_cloud_config(self, mock_config, mock_workspace):
        """No global cloud config — workspace-only values returned."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'namespace': 'team-a-ns',
                    }
                }
            }
        })

        result = skypilot_config.get_workspace_cloud('kubernetes')
        assert result['namespace'] == 'team-a-ns'

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_nested_dict_deep_merged(self, mock_config, mock_workspace):
        """Nested dicts are deep-merged, not replaced."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({
            'kubernetes': {
                'kueue': {
                    'local_queue_name': 'global-queue',
                },
                'allowed_contexts': ['ctx-a'],
            },
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'kueue': {
                            'local_queue_name': 'team-a-queue',
                        }
                    }
                }
            }
        })

        result = skypilot_config.get_workspace_cloud('kubernetes')
        assert result['kueue']['local_queue_name'] == 'team-a-queue'
        assert result['allowed_contexts'] == ['ctx-a']

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_does_not_mutate_global_config(self, mock_config, mock_workspace):
        """Deep-merge must not mutate the original global config."""
        mock_workspace.return_value = 'team-a'
        original_config = _make_config({
            'gcp': {
                'project_id': 'global-project',
                'disabled': False,
            },
            'workspaces': {
                'team-a': {
                    'gcp': {
                        'project_id': 'team-a-project',
                    }
                }
            }
        })
        mock_config.return_value = original_config

        skypilot_config.get_workspace_cloud('gcp')
        # Original global config should be unchanged
        assert original_config['gcp']['project_id'] == 'global-project'

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_list_values_are_replaced(self, mock_config, mock_workspace):
        """List-valued fields are replaced, not concatenated."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({
            'kubernetes': {
                'allowed_contexts': ['ctx-a', 'ctx-b', 'ctx-c'],
                'namespace': 'global-ns',
            },
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'allowed_contexts': ['ctx-a'],
                    }
                }
            }
        })

        result = skypilot_config.get_workspace_cloud('kubernetes')
        # Workspace list replaces global list entirely
        assert result['allowed_contexts'] == ['ctx-a']
        # Non-overridden fields still inherited
        assert result['namespace'] == 'global-ns'

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_null_overrides_global_value(self, mock_config, mock_workspace):
        """Setting a field to None in workspace clears the global value."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({
            'gcp': {
                'project_id': 'global-project',
                'disabled': False,
            },
            'workspaces': {
                'team-a': {
                    'gcp': {
                        'project_id': None,
                    }
                }
            }
        })

        result = skypilot_config.get_workspace_cloud('gcp')
        # Null explicitly overrides the inherited value
        assert result['project_id'] is None
        # Other fields still inherited
        assert result['disabled'] is False
