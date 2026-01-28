# Smoke tests for Recipe Hub feature
# Tests the ability to launch clusters, jobs, and pools using the recipes:<name> syntax
#
# Example usage:
# Run all recipe tests:
# > pytest tests/smoke_tests/test_recipes.py
#
# Run a specific test:
# > pytest tests/smoke_tests/test_recipes.py::test_recipe_cluster_launch
#
# Run with a specific cloud:
# > pytest tests/smoke_tests/test_recipes.py --generic-cloud aws

import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky.recipes import core as recipes_core
from sky.recipes import db as recipes_db
from sky.recipes.utils import RecipeType

# Test recipe names - must follow the naming convention (letters, numbers, dashes)
TEST_CLUSTER_RECIPE = 'test-smoke-cluster'
TEST_JOB_RECIPE = 'test-smoke-job'
TEST_POOL_RECIPE = 'test-smoke-pool'
TEST_VOLUME_RECIPE = 'test-smoke-volume'


def _create_test_recipe(name: str, recipe_type: RecipeType, content: str):
    """Create a test recipe in the database, deleting any existing one first."""
    # Delete existing recipe if it exists
    try:
        recipes_db.delete_recipe(name, user_id='test-user')
    except Exception:
        pass

    # Create the recipe
    recipes_core.create_recipe(
        name=name,
        content=content,
        recipe_type=recipe_type,
        user_id='test-user',
        user_name='Test User',
        description=f'Smoke test recipe for {recipe_type.value}',
    )


def _delete_test_recipe(name: str):
    """Delete a test recipe from the database."""
    try:
        recipes_db.delete_recipe(name, user_id='test-user')
    except Exception:
        pass


# Minimal YAML configs for testing
CLUSTER_YAML = textwrap.dedent("""
resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Recipe cluster test completed successfully"
""").strip()

JOB_YAML = textwrap.dedent("""
resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Recipe managed job test completed successfully"
""").strip()

POOL_YAML = textwrap.dedent("""
resources:
  cpus: 2+
  memory: 4+

pool:
  workers: 1
""").strip()

VOLUME_YAML = textwrap.dedent("""
name: test-smoke-vol
type: k8s-pvc
size: 1Gi
""").strip()


@pytest.fixture(scope='function')
def cluster_recipe():
    """Create a cluster recipe for testing."""
    _create_test_recipe(TEST_CLUSTER_RECIPE, RecipeType.CLUSTER, CLUSTER_YAML)
    yield TEST_CLUSTER_RECIPE
    _delete_test_recipe(TEST_CLUSTER_RECIPE)


@pytest.fixture(scope='function')
def job_recipe():
    """Create a job recipe for testing."""
    _create_test_recipe(TEST_JOB_RECIPE, RecipeType.JOB, JOB_YAML)
    yield TEST_JOB_RECIPE
    _delete_test_recipe(TEST_JOB_RECIPE)


@pytest.fixture(scope='function')
def pool_recipe():
    """Create a pool recipe for testing."""
    _create_test_recipe(TEST_POOL_RECIPE, RecipeType.POOL, POOL_YAML)
    yield TEST_POOL_RECIPE
    _delete_test_recipe(TEST_POOL_RECIPE)


@pytest.fixture(scope='function')
def volume_recipe():
    """Create a volume recipe for testing."""
    _create_test_recipe(TEST_VOLUME_RECIPE, RecipeType.VOLUME, VOLUME_YAML)
    yield TEST_VOLUME_RECIPE
    _delete_test_recipe(TEST_VOLUME_RECIPE)


# ---------- Recipe Cluster Launch ----------
def test_recipe_cluster_launch(generic_cloud: str, cluster_recipe: str):
    """Test launching a cluster using recipes:<name> syntax."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'recipe_cluster_launch',
        [
            # Launch cluster using recipe reference
            f'sky launch -y -c {name} --infra {generic_cloud} recipes:{cluster_recipe}',
            # Verify job succeeded
            f'sky logs {name} 1 --status',
            f'sky logs {name} --status | grep "Job 1: SUCCEEDED"',
            # Verify the run output
            f'sky logs {name} 1 | grep "Recipe cluster test completed successfully"',
        ],
        f'sky down -y {name}',
        smoke_tests_utils.get_timeout(generic_cloud),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Recipe Managed Job Launch ----------
def test_recipe_managed_job_launch(generic_cloud: str, job_recipe: str):
    """Test launching a managed job using recipes:<name> syntax."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'recipe_managed_job_launch',
        [
            # Launch managed job using recipe reference
            f'sky jobs launch -y -n {name} --infra {generic_cloud} recipes:{job_recipe}',
            # Wait for job to complete
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=smoke_tests_utils.get_timeout(generic_cloud),
            ),
            # Verify the output
            f'sky jobs logs -n {name} | grep "Recipe managed job test completed successfully"',
        ],
        f'sky jobs cancel -n {name} -y; sleep 5',
        smoke_tests_utils.get_timeout(generic_cloud),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Recipe Pool Launch ----------
@pytest.mark.no_remote_server  # Pool tests may have resource conflicts
def test_recipe_pool_launch(generic_cloud: str, pool_recipe: str):
    """Test creating a job pool using recipes:<name> syntax."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'recipe_pool_launch',
        [
            # Create pool using recipe reference
            f's=$(sky jobs pool apply -p {name} --infra {generic_cloud} recipes:{pool_recipe} -y); '
            'echo "$s"; '
            'echo "$s" | grep "Successfully created pool"',
            # Wait for pool to be ready
            f'start_time=$SECONDS; '
            f'while true; do '
            f'if (( $SECONDS - $start_time > 300 )); then '
            f'  echo "Timeout waiting for pool to be ready"; exit 1; '
            f'fi; '
            f's=$(sky jobs pool status {name}); '
            f'echo "$s"; '
            f'if echo "$s" | grep "READY"; then break; fi; '
            f'if echo "$s" | grep "FAILED"; then exit 1; fi; '
            f'sleep 10; '
            f'done',
        ],
        f'sky jobs pool down {name} -y',
        smoke_tests_utils.get_timeout(generic_cloud),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Recipe Volume Apply (Kubernetes only) ----------
@pytest.mark.kubernetes
def test_recipe_volume_apply(volume_recipe: str):
    """Test creating a volume using recipes:<name> syntax (Kubernetes only)."""
    name = smoke_tests_utils.get_cluster_name()
    volume_name = f'{name}-vol'
    test = smoke_tests_utils.Test(
        'recipe_volume_apply',
        [
            # Create volume using recipe reference
            f's=$(sky volumes apply -y -n {volume_name} --infra kubernetes recipes:{volume_recipe}); '
            'echo "$s"; '
            'echo "$s" | grep -i "volume"',
            # Verify volume was created
            f'sky volumes ls | grep "{volume_name}"',
        ],
        f'sky volumes delete {volume_name} -y || true',
        timeout=120,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Recipe Not Found Error ----------
def test_recipe_not_found(generic_cloud: str):
    """Test that launching with a non-existent recipe gives a clear error."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'recipe_not_found',
        [
            # Try to launch with non-existent recipe - should fail
            f'sky launch -y -c {name} --infra {generic_cloud} recipes:nonexistent-recipe-xyz && exit 1 || '
            'echo "Expected failure for non-existent recipe"',
        ],
        f'sky down -y {name} 2>/dev/null || true',
        timeout=60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Recipe Invalid Name Error ----------
def test_recipe_invalid_name():
    """Test that creating a recipe with invalid name gives a clear error."""
    test = smoke_tests_utils.Test(
        'recipe_invalid_name',
        [
            # Try to create recipe with invalid name (contains underscore)
            lambda: _test_invalid_recipe_name('invalid_name'),
            # Try to create recipe with invalid name (contains dot)
            lambda: _test_invalid_recipe_name('invalid.name'),
            # Try to create recipe with invalid name (starts with number)
            lambda: _test_invalid_recipe_name('123invalid'),
        ],
        None,
        timeout=30,
    )
    smoke_tests_utils.run_one_test(test)


def _test_invalid_recipe_name(name: str):
    """Helper to test that an invalid recipe name raises an error."""
    from sky import exceptions
    try:
        recipes_core.create_recipe(
            name=name,
            content='resources:\n  cpus: 1',
            recipe_type=RecipeType.CLUSTER,
            user_id='test-user',
        )
        raise AssertionError(
            f'Expected InvalidRecipeNameError for name: {name}')
    except exceptions.InvalidRecipeNameError:
        print(f'Correctly rejected invalid recipe name: {name}')
