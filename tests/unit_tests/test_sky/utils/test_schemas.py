"""Tests for schemas.py"""
import unittest

import jsonschema

from sky.skylet import constants
from sky.utils import schemas


class TestResourcesSchema(unittest.TestCase):
    """Tests for the resources schema in schemas.py"""

    def test_valid_infra_configs(self):
        """Test validation of valid infra field configs."""
        resources_schema = schemas.get_resources_schema()

        # Valid infra configurations
        valid_infra_configs = [
            {
                'infra': 'aws'
            },
            {
                'infra': 'gcp'
            },
            {
                'infra': 'azure'
            },
            {
                'infra': 'kubernetes'
            },
            {
                'infra': 'aws/us-east-1'
            },
            {
                'infra': 'aws/us-east-1/us-east-1a'
            },
            {
                'infra': 'gcp/us-central1'
            },
            {
                'infra': 'k8s/my-cluster-ctx'
            },
            {
                'infra': 'kubernetes/my/complex/context/path'
            },
            {
                'infra': '*'
            },
            {
                'infra': '*/us-east-1'
            },
            {
                'infra': '*/us-east-1/us-east-1a'
            },
            {
                'infra': '*/*'
            },
            {
                'infra': '*/*/us-east-1a'
            },
        ]

        for config in valid_infra_configs:
            # Should not raise an exception
            jsonschema.validate(instance=config, schema=resources_schema)

    def test_invalid_infra_type(self):
        """Test validation rejects invalid infra field types."""
        resources_schema = schemas.get_resources_schema()

        # Invalid infra configurations - wrong type
        invalid_type_config = {'infra': 123}  # Not a string
        with self.assertRaises(jsonschema.exceptions.ValidationError):
            jsonschema.validate(instance=invalid_type_config,
                                schema=resources_schema)

    def test_invalid_infra_format(self):
        """Test validation rejects invalid infra field formats."""
        resources_schema = schemas.get_resources_schema()

        # Invalid formats
        invalid_formats = [
            {
                'infra': 'aws/'
            },  # Trailing slash without region
            {
                'infra': 'aws//us-east-1a'
            },  # Empty region
            {
                'infra': '/us-east-1'
            },  # Missing cloud
            {
                'infra': 'aws/us-east-1/zone/extra'
            },  # Too many segments
            {
                'infra': 'invalid-cloud/us-east-1'
            },  # Invalid cloud name
            {
                'infra': 'invalid-cloud'
            },  # Invalid cloud name without region
            {
                'infra': '**/us-east-1'
            },  # Multiple asterisks (invalid syntax)
        ]

        for config in invalid_formats:
            with self.assertRaises(
                    jsonschema.exceptions.ValidationError,
                    msg=f"Expected '{config['infra']}' to be rejected"):
                jsonschema.validate(instance=config, schema=resources_schema)

    def test_valid_priority_configs(self):
        """Test validation of valid priority field configs."""
        resources_schema = schemas.get_resources_schema()

        # Valid priority configurations
        valid_priority_configs = [
            {
                'priority': 0
            },  # Minimum value
            {
                'priority': 500
            },  # Middle value
            {
                'priority': 1000
            },  # Maximum value
            {
                'cpus': 4,
                'priority': 750
            },  # With other fields
        ]

        for config in valid_priority_configs:
            # Should not raise an exception
            jsonschema.validate(instance=config, schema=resources_schema)

    def test_invalid_priority_configs(self):
        """Test validation rejects invalid priority field configs."""
        resources_schema = schemas.get_resources_schema()

        # Invalid priority configurations
        invalid_priority_configs = [
            {
                'priority': constants.MIN_PRIORITY - 1
            },  # Below minimum
            {
                'priority': constants.MAX_PRIORITY + 1
            },  # Above maximum
            {
                'priority': 'high'
            },  # Not an integer
            {
                'priority': 500.5
            },  # Not an integer
            {
                'priority': None
            },  # None (should be omitted instead)
            {
                'priority': True
            },  # Boolean
        ]

        for config in invalid_priority_configs:
            with self.assertRaises(
                    jsonschema.exceptions.ValidationError,
                    msg=f"Expected priority config {config} to be rejected"):
                jsonschema.validate(instance=config, schema=resources_schema)


class TestWorkspaceSchema(unittest.TestCase):
    """Tests for the workspace schema in schemas.py"""

    def setUp(self):
        """Set up test fixtures."""
        self.config_schema = schemas.get_config_schema()
        self.workspaces_schema = self.config_schema['properties']['workspaces']

    def test_valid_workspace_configs(self):
        """Test validation of valid workspace configurations."""
        # Valid workspace configurations
        valid_workspace_configs = [
            # Empty workspace
            {},
            # Workspace with disabled cloud
            {
                'my-workspace': {
                    'aws': {
                        'disabled': True
                    }
                }
            },
            # GCP with project_id
            {
                'my-workspace': {
                    'gcp': {
                        'project_id': 'my-project',
                        'disabled': False
                    }
                }
            },
            # GCP with only project_id
            {
                'my-workspace': {
                    'gcp': {
                        'project_id': 'my-project'
                    }
                }
            },
            # GCP with only disabled
            {
                'my-workspace': {
                    'gcp': {
                        'disabled': True
                    }
                }
            },
            # Multiple clouds
            {
                'my-workspace': {
                    'aws': {
                        'disabled': False
                    },
                    'gcp': {
                        'project_id': 'my-project',
                        'disabled': False
                    },
                    'azure': {
                        'disabled': True
                    }
                }
            },
            # Multiple workspaces
            {
                'workspace-1': {
                    'aws': {
                        'disabled': False
                    }
                },
                'workspace-2': {
                    'gcp': {
                        'project_id': 'other-project'
                    }
                }
            }
        ]

        for config in valid_workspace_configs:
            # Should not raise an exception
            try:
                jsonschema.validate(instance=config,
                                    schema=self.workspaces_schema)
            except jsonschema.exceptions.ValidationError as e:
                self.fail(f"Valid config {config} was rejected: {e}")

    def test_non_gcp_clouds_only_allow_disabled(self):
        """Test that non-GCP clouds only allow 'disabled' property."""
        # Test that all non-GCP clouds only accept 'disabled' property
        non_gcp_clouds = ['aws', 'azure', 'kubernetes', 'oci', 'nebius']

        for cloud in non_gcp_clouds:
            # Valid: only disabled
            valid_config = {'my-workspace': {cloud: {'disabled': True}}}
            try:
                jsonschema.validate(instance=valid_config,
                                    schema=self.workspaces_schema)
            except jsonschema.exceptions.ValidationError as e:
                self.fail(f"Valid config for {cloud} was rejected: {e}")

            # Invalid: additional property should be rejected
            invalid_config = {
                'my-workspace': {
                    cloud: {
                        'disabled': True,
                        'project_id': 'should-not-be-allowed'
                    }
                }
            }
            with self.assertRaises(
                    jsonschema.exceptions.ValidationError,
                    msg=f"Config with extra property for {cloud} should be "
                    f"rejected"):
                jsonschema.validate(instance=invalid_config,
                                    schema=self.workspaces_schema)

    def test_gcp_allows_project_id_and_disabled(self):
        """Test that GCP allows both 'project_id' and 'disabled' properties."""
        # Valid: both project_id and disabled
        valid_configs = [{
            'my-workspace': {
                'gcp': {
                    'project_id': 'my-project',
                    'disabled': False
                }
            }
        }, {
            'my-workspace': {
                'gcp': {
                    'project_id': 'my-project'
                }
            }
        }, {
            'my-workspace': {
                'gcp': {
                    'disabled': True
                }
            }
        }]

        for config in valid_configs:
            try:
                jsonschema.validate(instance=config,
                                    schema=self.workspaces_schema)
            except jsonschema.exceptions.ValidationError as e:
                self.fail(f"Valid GCP config {config} was rejected: {e}")

    def test_gcp_rejects_invalid_additional_properties(self):
        """Test that GCP rejects invalid additional properties."""
        # Invalid: additional property not allowed for GCP either
        invalid_config = {
            'my-workspace': {
                'gcp': {
                    'project_id': 'my-project',
                    'disabled': False,
                    'invalid_property': 'should-not-be-allowed'
                }
            }
        }
        with self.assertRaises(jsonschema.exceptions.ValidationError):
            jsonschema.validate(instance=invalid_config,
                                schema=self.workspaces_schema)

    def test_invalid_workspace_types(self):
        """Test validation rejects invalid workspace types."""
        # Invalid types
        invalid_configs = [
            'string-not-object',  # Should be object, not string
            123,  # Should be object, not number
            ['array'],  # Should be object, not array
            {
                'my-workspace': 'should-be-object'  # Workspace should be object
            },
            {
                'my-workspace': {
                    'aws': 'should-be-object'  # Cloud config should be object
                }
            },
            {
                'my-workspace': {
                    'gcp': {
                        'project_id': 123  # project_id should be string
                    }
                }
            },
            {
                'my-workspace': {
                    'aws': {
                        'disabled': 'should-be-boolean'  # disabled should be bool
                    }
                }
            }
        ]

        for invalid_config in invalid_configs:
            with self.assertRaises(
                    jsonschema.exceptions.ValidationError,
                    msg=f"Invalid config {invalid_config} should be rejected"):
                jsonschema.validate(instance=invalid_config,
                                    schema=self.workspaces_schema)

    def test_unknown_cloud_names_rejected(self):
        """Test that unknown cloud names are rejected."""
        invalid_config = {'my-workspace': {'unknown-cloud': {'disabled': True}}}
        with self.assertRaises(jsonschema.exceptions.ValidationError):
            jsonschema.validate(instance=invalid_config,
                                schema=self.workspaces_schema)

    def test_only_lowercase_cloud_names_allowed(self):
        """Test that only lowercase cloud names are allowed."""
        # Valid: lowercase cloud names
        valid_config = {
            'my-workspace': {
                'cloudflare': {  # Special cloud
                    'disabled': False
                }
            }
        }
        try:
            jsonschema.validate(instance=valid_config,
                                schema=self.workspaces_schema)
        except jsonschema.exceptions.ValidationError as e:
            self.fail(f"Valid lowercase config was rejected: {e}")

        # Invalid: uppercase cloud names should be rejected
        invalid_configs = [
            {
                'my-workspace': {
                    'AWS': {  # Uppercase
                        'disabled': True
                    }
                }
            },
            {
                'my-workspace': {
                    'GCP': {  # Uppercase GCP
                        'project_id': 'my-project'
                    }
                }
            },
            {
                'my-workspace': {
                    'Azure': {  # Mixed case
                        'disabled': False
                    }
                }
            }
        ]

        for config in invalid_configs:
            with self.assertRaises(
                    jsonschema.exceptions.ValidationError,
                    msg=f"Uppercase cloud config {config} should be rejected"):
                jsonschema.validate(instance=config,
                                    schema=self.workspaces_schema)


class TestKubernetesSchema(unittest.TestCase):
    """Tests for the kubernetes schema in schemas.py."""

    def setUp(self):
        self.config_schema = schemas.get_config_schema()
        self.k8s_schema = self.config_schema['properties']['kubernetes']

    def test_context_configs_allows_remote_identity(self):
        """Test that context_configs allows remote_identity."""
        valid_config = {
            'context_configs': {
                'my-context': {
                    'remote_identity': 'my-service-account'
                }
            }
        }
        jsonschema.validate(instance=valid_config, schema=self.k8s_schema)


if __name__ == "__main__":
    unittest.main()
