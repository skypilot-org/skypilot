"""Unit tests for Recipe Hub core functionality.
Tests validation of recipes against SkyPilot schema.
"""
import pytest

from sky.recipes import core as recipes_core


class TestRecipeValidation:
    """Tests for recipe validation."""

    def test_create_invalid_yaml_syntax(self):
        """Test that creating a recipe with invalid syntax fails."""
        invalid_yaml = """
name: test
  bad_indentation: true
run: echo hello
"""
        with pytest.raises(ValueError, match='Invalid YAML syntax'):
            recipes_core._validate_skypilot_yaml(invalid_yaml, 'cluster')

    def test_create_invalid_yaml_not_dict(self):
        """Test that creating a recipe that's not a dict fails."""
        invalid_yaml = "- item1\n- item2\n- item3"
        with pytest.raises(ValueError,
                           match='YAML must be a dictionary/mapping'):
            recipes_core._validate_skypilot_yaml(invalid_yaml, 'cluster')

    def test_create_empty_yaml(self):
        """Test that creating an empty recipe fails."""
        empty_yaml = ""
        with pytest.raises(ValueError, match='YAML content is empty'):
            recipes_core._validate_skypilot_yaml(empty_yaml, 'cluster')

    def test_create_yaml_with_invalid_field(self):
        """Test that creating a YAML with only invalid fields fails."""
        # This YAML has no valid SkyPilot fields
        invalid_yaml = """
name3: Lloyd
random_field: value
"""
        with pytest.raises(ValueError, match='Invalid task YAML'):
            recipes_core._validate_skypilot_yaml(invalid_yaml, 'cluster')

    def test_create_job_yaml_with_invalid_service_section(self):
        """Test that creating a YAML with an invalid service section fails.
        
        The service section must have valid fields.
        """
        invalid_yaml = """
service:
  invalid_field: /health
  replicas: 1
  
resources:
  cpus: 2
  
run: echo hello
"""
        with pytest.raises(ValueError, match='Invalid recipe YAML'):
            recipes_core._validate_skypilot_yaml(invalid_yaml, 'serve')

    def test_create_serve_yaml_without_service_section(self):
        """Test that creating a recipe without service section fails."""
        invalid_serve_yaml = """
resources:
  cpus: 2
run: python server.py
"""
        with pytest.raises(
                ValueError,
                match="Service YAML must contain a 'service' section"):
            recipes_core._validate_skypilot_yaml(invalid_serve_yaml, 'serve')

    def test_create_pool_yaml_without_pool_section(self):
        """Test that creating a recipe without pool section fails."""
        invalid_pool_yaml = """
resources:
  cpus: 2
run: echo hello
"""
        with pytest.raises(ValueError,
                           match="Pool YAML must contain a 'pool' section"):
            recipes_core._validate_skypilot_yaml(invalid_pool_yaml, 'pool')

    def test_valid_cluster_yaml(self):
        """Test that a valid cluster YAML passes validation."""
        valid_yaml = """
resources:
  cpus: 2
run: echo hello
"""
        # Should not raise
        recipes_core._validate_skypilot_yaml(valid_yaml, 'cluster')

    def test_valid_job_yaml(self):
        """Test that a valid job YAML passes validation."""
        valid_yaml = """
resources:
  cpus: 2
run: echo hello
"""
        # Should not raise
        recipes_core._validate_skypilot_yaml(valid_yaml, 'job')

    def test_invalid_yaml_type(self):
        """Test that an invalid yaml_type is rejected."""
        valid_yaml = """
resources:
  cpus: 2
run: echo hello
"""
        with pytest.raises(ValueError, match="yaml_type must be one of"):
            recipes_core.create_recipe(
                name='test',
                content=valid_yaml,
                recipe_type='invalid_type',
                user_id='test_user',
            )

    def test_yaml_with_invalid_resources(self):
        """Test that a YAML with invalid resource specifications fails."""
        invalid_yaml = """
resources:
  invalid_resource: 999
run: echo hello
"""
        with pytest.raises(ValueError, match='Invalid resources YAML'):
            recipes_core._validate_skypilot_yaml(invalid_yaml, 'cluster')

    def test_yaml_with_completely_invalid_structure(self):
        """Test that a YAML with completely invalid structure fails."""
        invalid_yaml = """
not_a_valid_field: value
another_invalid: 123
"""
        with pytest.raises(ValueError, match='Invalid task YAML'):
            recipes_core._validate_skypilot_yaml(invalid_yaml, 'cluster')

    def test_cluster_yaml_minimal(self):
        """Test that a minimal cluster YAML with just run command works."""
        valid_yaml = """
run: echo hello world
"""
        # Should not raise - minimal YAML with just a run command is valid
        recipes_core._validate_skypilot_yaml(valid_yaml, 'cluster')