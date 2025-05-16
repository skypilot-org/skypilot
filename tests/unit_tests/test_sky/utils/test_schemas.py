"""Tests for schemas.py"""
import unittest

import jsonschema

from sky.utils import schemas


class TestResourcesSchema(unittest.TestCase):
    """Tests for the resources schema in schemas.py"""

    def test_valid_infra_configs(self):
        """Test validation of valid infra field configs."""
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
            {'infra': '*/*'},
            {'infra': '*/*/us-east-1a'},
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
            jsonschema.validate(instance=invalid_type_config, schema=resources_schema)
            
    def test_invalid_infra_format(self):
        """Test validation rejects invalid infra field formats."""
        resources_schema = schemas.get_resources_schema()
        
        # Invalid formats
        invalid_formats = [
            {'infra': 'aws/'},  # Trailing slash without region
            {'infra': 'aws//us-east-1a'},  # Empty region
            {'infra': '/us-east-1'},  # Missing cloud
            {'infra': 'aws/us-east-1/zone/extra'},  # Too many segments
            {'infra': 'invalid-cloud/us-east-1'},  # Invalid cloud name
            {'infra': 'invalid-cloud'},  # Invalid cloud name without region
            {'infra': '**/us-east-1'},  # Multiple asterisks (invalid syntax)
        ]
        
        for config in invalid_formats:
            with self.assertRaises(jsonschema.exceptions.ValidationError, 
                                  msg=f"Expected '{config['infra']}' to be rejected"):
                jsonschema.validate(instance=config, schema=resources_schema)


if __name__ == "__main__":
    unittest.main() 
 