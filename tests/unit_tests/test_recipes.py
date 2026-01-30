"""Unit tests for Recipe Hub core functionality.
Tests validation of recipes against SkyPilot schema.
"""
import textwrap

import pytest

from sky import exceptions
from sky.recipes import core as recipes_core
from sky.recipes.utils import RecipeType
from sky.utils import common_utils


class TestRecipeValidation:
    """Tests for recipe validation."""

    def test_create_invalid_yaml_syntax(self):
        """Test that creating a recipe with invalid syntax fails."""
        invalid_yaml = textwrap.dedent("""
        name: test
         bad_indentation: true
        run: echo hello
        """).strip()
        with pytest.raises(ValueError, match='Invalid YAML syntax'):
            recipes_core._validate_skypilot_yaml(invalid_yaml,
                                                 RecipeType.CLUSTER)

    def test_create_invalid_yaml_not_dict(self):
        """Test that creating a recipe that's not a dict fails."""
        invalid_yaml = "- item1\n- item2\n- item3"
        with pytest.raises(ValueError,
                           match='YAML must be a dictionary/mapping'):
            recipes_core._validate_skypilot_yaml(invalid_yaml,
                                                 RecipeType.CLUSTER)

    def test_create_empty_yaml(self):
        """Test that creating an empty recipe fails."""
        empty_yaml = ""
        with pytest.raises(ValueError, match='YAML content is empty'):
            recipes_core._validate_skypilot_yaml(empty_yaml, RecipeType.CLUSTER)

    def test_create_yaml_with_invalid_field(self):
        """Test that creating a YAML with only invalid fields fails."""
        # This YAML has no valid SkyPilot fields
        invalid_yaml = textwrap.dedent("""
        name3: Lloyd
        random_field: value
        """).strip()
        with pytest.raises(ValueError, match='Invalid task YAML'):
            recipes_core._validate_skypilot_yaml(invalid_yaml,
                                                 RecipeType.CLUSTER)

    def test_create_pool_yaml_without_pool_section(self):
        """Test that creating a recipe without pool section fails."""
        invalid_pool_yaml = textwrap.dedent("""
        resources:
          cpus: 2
        run: echo hello
        """).strip()
        with pytest.raises(ValueError,
                           match="Pool YAML must contain a 'pool' section"):
            recipes_core._validate_skypilot_yaml(invalid_pool_yaml,
                                                 RecipeType.POOL)

    def test_valid_cluster_yaml(self):
        """Test that a valid cluster YAML passes validation."""
        valid_yaml = textwrap.dedent("""
        resources:
          cpus: 2
        run: echo hello
        """).strip()
        # Should not raise
        recipes_core._validate_skypilot_yaml(valid_yaml, RecipeType.CLUSTER)

    def test_valid_job_yaml(self):
        """Test that a valid job YAML passes validation."""
        valid_yaml = textwrap.dedent("""
        resources:
          cpus: 2
        run: echo hello
        """).strip()
        # Should not raise
        recipes_core._validate_skypilot_yaml(valid_yaml, RecipeType.JOB)

    def test_invalid_recipe_type(self):
        """Test that an invalid recipe_type is rejected."""
        valid_yaml = textwrap.dedent("""
        resources:
          cpus: 2
        run: echo hello
        """).strip()
        with pytest.raises(ValueError, match='Invalid recipe type'):
            recipes_core.create_recipe(
                name='test',
                content=valid_yaml,
                recipe_type='invalid_type',
                user_id='test_user',
            )

    def test_yaml_with_invalid_resources(self):
        """Test that a YAML with invalid resource specifications fails."""
        invalid_yaml = textwrap.dedent("""
        resources:
          invalid_resource: 999
        run: echo hello
        """).strip()
        with pytest.raises(ValueError, match='Invalid resources YAML'):
            recipes_core._validate_skypilot_yaml(invalid_yaml,
                                                 RecipeType.CLUSTER)

    def test_yaml_with_completely_invalid_structure(self):
        """Test that a YAML with completely invalid structure fails."""
        invalid_yaml = textwrap.dedent("""
        not_a_valid_field: value
        another_invalid: 123
        """).strip()
        with pytest.raises(ValueError, match='Invalid task YAML'):
            recipes_core._validate_skypilot_yaml(invalid_yaml,
                                                 RecipeType.CLUSTER)

    def test_cluster_yaml_minimal(self):
        """Test that a minimal cluster YAML with just run command works."""
        valid_yaml = textwrap.dedent("""
        run: echo hello world
        """).strip()
        # Should not raise - minimal YAML with just a run command is valid
        recipes_core._validate_skypilot_yaml(valid_yaml, RecipeType.CLUSTER)

    # =========================================================================
    # Tests for local path validation (workdir and file_mounts)
    # =========================================================================

    def test_local_workdir_rejected(self):
        """Test that local workdir paths are rejected in recipes."""
        yaml_with_local_workdir = textwrap.dedent("""
        workdir: /path/to/local/dir
        run: python train.py
        """).strip()
        with pytest.raises(ValueError,
                           match='Local workdir paths are not allowed'):
            recipes_core._validate_skypilot_yaml(yaml_with_local_workdir,
                                                 RecipeType.CLUSTER)

    def test_local_workdir_relative_path_rejected(self):
        """Test that relative workdir paths are rejected in recipes."""
        yaml_with_relative_workdir = textwrap.dedent("""
        workdir: ./my-project
        run: python train.py
        """).strip()
        with pytest.raises(ValueError,
                           match='Local workdir paths are not allowed'):
            recipes_core._validate_skypilot_yaml(yaml_with_relative_workdir,
                                                 RecipeType.CLUSTER)

    def test_git_workdir_allowed(self):
        """Test that git URL workdir is allowed in recipes."""
        yaml_with_git_workdir = textwrap.dedent("""
        workdir:
          url: https://github.com/user/repo
          ref: main
        run: python train.py
        """).strip()
        # Should not raise
        recipes_core._validate_skypilot_yaml(yaml_with_git_workdir,
                                             RecipeType.CLUSTER)

    def test_git_workdir_no_ref_allowed(self):
        """Test that git URL workdir without ref is allowed in recipes."""
        yaml_with_git_workdir = textwrap.dedent("""
        workdir:
          url: https://github.com/user/repo
        run: python train.py
        """).strip()
        # Should not raise
        recipes_core._validate_skypilot_yaml(yaml_with_git_workdir,
                                             RecipeType.CLUSTER)

    def test_local_file_mount_rejected(self):
        """Test that local file mount sources are rejected in recipes."""
        yaml_with_local_mount = textwrap.dedent("""
        file_mounts:
          /remote/data: /local/path/to/data
        run: echo hello
        """).strip()
        with pytest.raises(ValueError,
                           match='Local file mounts are not allowed'):
            recipes_core._validate_skypilot_yaml(yaml_with_local_mount,
                                                 RecipeType.CLUSTER)

    def test_local_file_mount_relative_path_rejected(self):
        """Test that relative file mount paths are rejected in recipes."""
        yaml_with_relative_mount = textwrap.dedent("""
        file_mounts:
          /remote/data: ./local/data
        run: echo hello
        """).strip()
        with pytest.raises(ValueError,
                           match='Local file mounts are not allowed'):
            recipes_core._validate_skypilot_yaml(yaml_with_relative_mount,
                                                 RecipeType.CLUSTER)

    def test_cloud_file_mount_s3_allowed(self):
        """Test that S3 file mounts are allowed in recipes."""
        yaml_with_cloud_mount = textwrap.dedent("""
        file_mounts:
          /remote/data: s3://my-bucket/data
        run: echo hello
        """).strip()
        # Should not raise
        recipes_core._validate_skypilot_yaml(yaml_with_cloud_mount,
                                             RecipeType.CLUSTER)

    def test_cloud_file_mount_gs_allowed(self):
        """Test that GCS file mounts are allowed in recipes."""
        yaml_with_gcs_mount = textwrap.dedent("""
        file_mounts:
          /remote/data: gs://my-bucket/data
        run: echo hello
        """).strip()
        # Should not raise
        recipes_core._validate_skypilot_yaml(yaml_with_gcs_mount,
                                             RecipeType.CLUSTER)

    def test_mixed_file_mounts_one_local_rejected(self):
        """Test that mixed file mounts with one local source are rejected."""
        yaml_with_mixed_mounts = textwrap.dedent("""
        file_mounts:
          /remote/cloud-data: s3://my-bucket/data
          /remote/local-data: /local/path/to/data
        run: echo hello
        """).strip()
        with pytest.raises(ValueError,
                           match='Local file mounts are not allowed'):
            recipes_core._validate_skypilot_yaml(yaml_with_mixed_mounts,
                                                 RecipeType.CLUSTER)

    def test_inline_storage_mount_allowed(self):
        """Test that inline storage definitions (dicts) are allowed."""
        yaml_with_inline_storage = textwrap.dedent("""
        file_mounts:
          /remote/data:
            name: my-bucket
            source: s3://my-bucket/data
            mode: COPY
        run: echo hello
        """).strip()
        # Should not raise - dict sources are inline storage definitions
        recipes_core._validate_skypilot_yaml(yaml_with_inline_storage,
                                             RecipeType.CLUSTER)

    def test_no_workdir_no_file_mounts_allowed(self):
        """Test that recipes without workdir or file_mounts are valid."""
        simple_yaml = textwrap.dedent("""
        resources:
          cpus: 2
        run: echo hello
        """).strip()
        # Should not raise
        recipes_core._validate_skypilot_yaml(simple_yaml, RecipeType.CLUSTER)

    # =========================================================================
    # Tests for volume recipe validation
    # =========================================================================

    def test_valid_volume_yaml(self):
        """Test that a valid volume YAML passes validation."""
        valid_volume_yaml = textwrap.dedent("""
        name: my-volume
        type: k8s-pvc
        size: 100Gi
        """).strip()
        # Should not raise
        recipes_core._validate_skypilot_yaml(valid_volume_yaml,
                                             RecipeType.VOLUME)

    def test_volume_yaml_missing_name(self):
        """Test that volume YAML without name is rejected."""
        invalid_volume_yaml = textwrap.dedent("""
        type: k8s-pvc
        size: 100Gi
        """).strip()
        with pytest.raises(ValueError, match="'name' is a required property"):
            recipes_core._validate_skypilot_yaml(invalid_volume_yaml,
                                                 RecipeType.VOLUME)

    def test_volume_yaml_missing_type(self):
        """Test that volume YAML without type is rejected."""
        invalid_volume_yaml = textwrap.dedent("""
        name: my-volume
        size: 100Gi
        """).strip()
        with pytest.raises(ValueError, match="'type' is a required property"):
            recipes_core._validate_skypilot_yaml(invalid_volume_yaml,
                                                 RecipeType.VOLUME)

    def test_volume_yaml_invalid_type(self):
        """Test that volume YAML with invalid type is rejected."""
        invalid_volume_yaml = textwrap.dedent("""
        name: my-volume
        type: invalid-type
        size: 100Gi
        """).strip()
        with pytest.raises(ValueError, match='Invalid volume YAML'):
            recipes_core._validate_skypilot_yaml(invalid_volume_yaml,
                                                 RecipeType.VOLUME)


class TestRecipeNameValidation:
    """Tests for recipe name validation.

    These tests validate the recipe name format directly using
    check_recipe_name_is_valid to avoid any database interaction.
    """

    def test_invalid_name_with_underscore(self):
        """Test that recipe names with underscores are rejected."""
        with pytest.raises(exceptions.InvalidRecipeNameError):
            common_utils.check_recipe_name_is_valid('invalid_name')

    def test_invalid_name_with_dot(self):
        """Test that recipe names with dots are rejected."""
        with pytest.raises(exceptions.InvalidRecipeNameError):
            common_utils.check_recipe_name_is_valid('invalid.name')

    def test_invalid_name_starts_with_number(self):
        """Test that recipe names starting with numbers are rejected."""
        with pytest.raises(exceptions.InvalidRecipeNameError):
            common_utils.check_recipe_name_is_valid('123invalid')

    def test_invalid_name_with_spaces(self):
        """Test that recipe names with spaces are rejected."""
        with pytest.raises(exceptions.InvalidRecipeNameError):
            common_utils.check_recipe_name_is_valid('invalid name')

    def test_invalid_name_ends_with_dash(self):
        """Test that recipe names ending with dash are rejected."""
        with pytest.raises(exceptions.InvalidRecipeNameError):
            common_utils.check_recipe_name_is_valid('invalid-name-')

    def test_invalid_name_starts_with_dash(self):
        """Test that recipe names starting with dash are rejected."""
        with pytest.raises(exceptions.InvalidRecipeNameError):
            common_utils.check_recipe_name_is_valid('-invalid-name')

    def test_valid_name_simple(self):
        """Test that simple valid names are accepted."""
        # Should not raise
        common_utils.check_recipe_name_is_valid('valid-name')
        common_utils.check_recipe_name_is_valid('validname')
        common_utils.check_recipe_name_is_valid('valid123')

    def test_valid_name_with_numbers(self):
        """Test that names with numbers in the middle are accepted."""
        # Should not raise
        common_utils.check_recipe_name_is_valid('my-recipe-v2')
        common_utils.check_recipe_name_is_valid('llama3-70b-finetune')
