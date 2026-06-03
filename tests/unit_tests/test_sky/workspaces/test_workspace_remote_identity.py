"""Tests for per-workspace remote_identity resolution and schema."""
from unittest import mock

import jsonschema
import pytest

from sky import skypilot_config
from sky.utils import config_utils
from sky.utils import schemas


def _make_config(config_dict):
    return config_utils.Config(config_dict)


class TestWorkspaceRemoteIdentityResolution:
    """Test that remote_identity resolves workspace-first, then global."""

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_workspace_overrides_global_aws(self, mock_config,
                                            mock_workspace):
        """AWS workspace remote_identity takes precedence over global."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({
            'aws': {
                'remote_identity': 'arn:aws:iam::123:role/global-role',
            },
            'workspaces': {
                'team-a': {
                    'aws': {
                        'remote_identity':
                            'arn:aws:iam::123:role/team-a-role',
                    }
                }
            }
        })

        result = skypilot_config.get_effective_workspace_region_config(
            cloud='aws',
            keys=('remote_identity',),
            region=None,
            default_value=None)
        assert result == 'arn:aws:iam::123:role/team-a-role'

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_falls_back_to_global_when_no_workspace_override(
            self, mock_config, mock_workspace):
        """No workspace override — falls back to global."""
        mock_workspace.return_value = 'team-b'
        mock_config.return_value = _make_config({
            'kubernetes': {
                'remote_identity': 'global-sa',
            },
            'workspaces': {
                'team-b': {
                    'kubernetes': {}
                }
            }
        })

        result = skypilot_config.get_effective_workspace_region_config(
            cloud='kubernetes',
            keys=('remote_identity',),
            region=None,
            default_value=None)
        assert result == 'global-sa'

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_returns_default_when_no_config(self, mock_config,
                                            mock_workspace):
        """No workspace or global config — returns default_value."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({})

        result = skypilot_config.get_effective_workspace_region_config(
            cloud='gcp',
            keys=('remote_identity',),
            region=None,
            default_value='LOCAL_CREDENTIALS')
        assert result == 'LOCAL_CREDENTIALS'

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_k8s_workspace_remote_identity(self, mock_config,
                                           mock_workspace):
        """K8s workspace remote_identity resolves correctly."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({
            'kubernetes': {
                'remote_identity': 'default-sa',
            },
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'remote_identity': 'team-a-sa',
                    }
                }
            }
        })

        result = skypilot_config.get_effective_workspace_region_config(
            cloud='kubernetes',
            keys=('remote_identity',),
            region=None,
            default_value=None)
        assert result == 'team-a-sa'


class TestWorkspaceRemoteIdentitySchema:
    """Test that workspace schema accepts remote_identity fields."""

    def _get_schema(self):
        return schemas.get_config_schema()

    def test_aws_remote_identity_in_workspace(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'aws': {
                        'remote_identity':
                            'arn:aws:iam::123:role/team-a',
                    }
                }
            }
        }
        jsonschema.validate(config, schema)

    def test_gcp_remote_identity_in_workspace(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'gcp': {
                        'remote_identity': 'SERVICE_ACCOUNT',
                    }
                }
            }
        }
        jsonschema.validate(config, schema)

    def test_k8s_remote_identity_string_in_workspace(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'remote_identity': 'team-a-sa',
                    }
                }
            }
        }
        jsonschema.validate(config, schema)

    def test_k8s_remote_identity_dict_in_workspace(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'remote_identity': {
                            'eks-cluster': 'team-a-eks-sa',
                            'onprem-cluster': 'team-a-onprem-sa',
                        },
                    }
                }
            }
        }
        jsonschema.validate(config, schema)

    def test_k8s_remote_identity_in_context_configs(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'context_configs': {
                            'my-cluster': {
                                'remote_identity': 'cluster-specific-sa',
                            }
                        }
                    }
                }
            }
        }
        jsonschema.validate(config, schema)

    def test_invalid_type_rejected(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'remote_identity': 12345,
                    }
                }
            }
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, schema)
