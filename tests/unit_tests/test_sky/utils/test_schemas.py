"""Tests for schemas.py"""
import unittest

import jsonschema

from sky.utils import schemas


class TestResourcesSchema(unittest.TestCase):
    """Tests for the resources schema in schemas.py"""

    def test_infra_schema(self):
        """Test validation of the infra field in resources schema."""
        resources_schema = schemas.get_resources_schema()
        
        # Valid infra configurations
        valid_infra_configs = [
            {'infra': 'aws'},
            {'infra': 'gcp'},
            {'infra': 'azure'},
            {'infra': 'kubernetes'},
            {'infra': 'aws/us-east-1'},
            {'infra': 'aws/us-east-1/us-east-1a'},
            {'infra': 'gcp/us-central1'},
            {'infra': 'k8s/my-cluster-ctx'},
            {'infra': 'kubernetes/my/complex/context/path'},
            {'infra': '*'},
            {'infra': '*/us-east-1'},
            {'infra': '*/us-east-1/us-east-1a'},
        ]
        
        for config in valid_infra_configs:
            # Should not raise an exception
            jsonschema.validate(instance=config, schema=resources_schema)
        
        # Invalid infra configurations - wrong type
        invalid_type_config = {'infra': 123}  # Not a string
        with self.assertRaises(jsonschema.exceptions.ValidationError):
            jsonschema.validate(instance=invalid_type_config, schema=resources_schema)
            
        # Invalid infra configurations - wrong format or invalid cloud name
        invalid_format_configs = [
            {'infra': 'aws/'},  # Trailing slash without region
            {'infra': 'aws//us-east-1a'},  # Empty region
            {'infra': '/us-east-1'},  # Missing cloud
            {'infra': 'aws/us-east-1/zone/extra'},  # Too many segments
            {'infra': 'invalid-cloud/us-east-1'},  # Invalid cloud name
            {'infra': 'invalid-cloud'},  # Invalid cloud name without region
            {'infra': '**/us-east-1'},  # Multiple wildcards for cloud
            {'infra': '*/*/us-east-1a'},  # Multiple wildcards
        ]
        
        for config in invalid_format_configs:
            with self.assertRaises(jsonschema.exceptions.ValidationError):
                jsonschema.validate(instance=config, schema=resources_schema)


if __name__ == "__main__":
    unittest.main() 
