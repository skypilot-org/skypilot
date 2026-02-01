"""Core business logic for Recipe Hub.

This module provides the main entry points for recipe operations,
including CRUD operations and deployment functionality.
"""
from typing import Any, Dict, List, Optional, Tuple

import yaml

from sky import sky_logging
from sky import task as task_lib
from sky.data import data_utils
from sky.recipes import db as recipes_db
from sky.recipes.utils import RecipeType
from sky.utils import common_utils
from sky.utils import schemas

logger = sky_logging.init_logger(__name__)


def _validate_no_local_paths(config: Dict[str, Any]) -> None:
    """Validate that recipes don't contain local file paths.

    Recipes are shareable templates, so they cannot reference local files
    that wouldn't exist on other users' machines.

    Args:
        config: The parsed YAML config dictionary.

    Raises:
        ValueError: If local paths are found in workdir or file_mounts.
    """
    # Check workdir - string means local path, dict means git URL
    workdir = config.get('workdir')
    if workdir is not None and isinstance(workdir, str):
        raise ValueError('Local workdir paths are not allowed in recipes. '
                         'Use a git URL instead. Example:\n'
                         '  workdir:\n'
                         '    url: https://github.com/user/repo\n'
                         '    ref: main  # optional')

    # Check file_mounts - sources must be cloud URLs
    file_mounts = config.get('file_mounts')
    if file_mounts is not None:
        for target, source in file_mounts.items():
            # source can be a string (path/URL) or a dict (inline storage)
            if isinstance(source, str):
                if not data_utils.is_cloud_store_url(source):
                    raise ValueError(
                        f'Local file mounts are not allowed in recipes. '
                        f'Use cloud storage (s3://, gs://, etc.) instead. '
                        f'Found local path at file_mounts[{target!r}]: '
                        f'{source!r}')


def _validate_skypilot_yaml(content: str, recipe_type: RecipeType) -> None:
    """Validate YAML content against SkyPilot schema.

    Args:
        content: The YAML content string.
        recipe_type: Type of recipe.

    Raises:
        ValueError: If the YAML doesn't conform to SkyPilot schema.
    """
    try:
        # Parse YAML first
        config = yaml.safe_load(content)
        if config is None:
            raise ValueError('YAML content is empty')
        if not isinstance(config, dict):
            raise ValueError(
                'YAML must be a dictionary/mapping at the top level')

        # Validate no local paths in recipes (workdir must be git, file_mounts
        # must be cloud storage)
        _validate_no_local_paths(config)

        # Validate based on type
        if recipe_type == RecipeType.VOLUME:
            # Validate volume schema (handles required fields: name, type)
            common_utils.validate_schema(config, schemas.get_volume_schema(),
                                         'Invalid volume YAML: ')
        else:
            if recipe_type == RecipeType.POOL:
                # Pool YAMLs should have a 'pool' section
                if 'pool' not in config:
                    raise ValueError('Pool YAML must contain a \'pool\' '
                                     'section. Example:\n  pool:\n    name: '
                                     'my-pool')

            # Use Task.from_yaml_str for full schema validation
            # This validates resources, envs, setup, run, file_mounts, etc.
            task_lib.Task.from_yaml_str(content)

    except yaml.YAMLError as e:
        raise ValueError(f'Invalid YAML syntax: {e}') from e
    except Exception as e:
        # Re-raise ValueError as-is, wrap others
        if isinstance(e, ValueError):
            raise
        raise ValueError(f'Invalid SkyPilot YAML: {e}') from e


def get_recipe_content(recipe_name: str) -> Tuple[str, str]:
    """Get recipe content and type by name.

    This function is used by the CLI to fetch a recipe from the Hub
    when launching with recipes:<name>.

    Args:
        recipe_name: The recipe's unique name.

    Returns:
        Tuple of (recipe_content, recipe_type).

    Raises:
        ValueError: If recipe not found.
    """
    template = recipes_db.get_recipe(recipe_name)
    if template is None:
        raise ValueError(f'Recipe not found: {recipe_name}')
    return (template.content, template.recipe_type.value)


def list_recipes(
    user_id: Optional[str] = None,
    pinned_only: bool = False,
    my_recipes_only: bool = False,
    recipe_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List recipes with optional filters.

    By default, returns all pinned recipes plus recipes owned by the user.

    Args:
        user_id: The current user's ID (needed to see own recipes).
        pinned_only: If True, only return pinned recipes.
        my_recipes_only: If True, only return recipes owned by user_id.
        recipe_type: Filter by type ('cluster' or 'job' or 'pool' or 'volume').

    Returns:
        List of recipe dictionaries.
    """
    recipe_type = (None
                   if recipe_type is None else RecipeType.from_str(recipe_type))
    recipes = recipes_db.list_recipes(
        user_id=user_id,
        pinned_only=pinned_only,
        my_recipes_only=my_recipes_only,
        recipe_type=recipe_type,
    )
    return [r.to_dict() for r in recipes]


def get_recipe(recipe_name: str) -> Optional[Dict[str, Any]]:
    """Get a single recipe by name.

    Args:
        recipe_name: The recipe's unique name.

    Returns:
        Recipe dictionary if found, None otherwise.
    """
    recipe = recipes_db.get_recipe(recipe_name)
    if recipe is None:
        return None
    return recipe.to_dict()


def create_recipe(
    name: str,
    content: str,
    recipe_type: str,
    user_id: str,
    user_name: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new recipe.

    Args:
        name: Display name for the recipe.
        content: The YAML content.
        recipe_type: Type of recipe ('cluster' or 'job').
        user_id: ID of the user creating the recipe.
        user_name: Optional display name of the user.
        description: Optional description.

    Returns:
        The created recipe as a dictionary.

    Raises:
        ValueError: If the YAML content is invalid or recipe_type is invalid.
    """
    # Validate recipe_type using enum (raises ValueError with helpful message)
    recipe_type = RecipeType.from_str(recipe_type)

    # Validate content against SkyPilot schema
    _validate_skypilot_yaml(content, recipe_type)

    recipe = recipes_db.create_recipe(
        name=name,
        content=content,
        recipe_type=recipe_type,
        user_id=user_id,
        user_name=user_name,
        description=description,
    )
    return recipe.to_dict()


def update_recipe(
    recipe_name: str,
    user_id: str,
    user_name: Optional[str] = None,
    description: Optional[str] = None,
    content: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update a recipe.

    A recipe can only be updated if it's editable.
    Note: Recipe names cannot be changed as they are the primary identifier.

    Args:
        recipe_name: The recipe's unique name.
        user_id: ID of the user making the update.
        user_name: Name of the user making the update.
        description: New description (if updating).
        content: New YAML content (if updating).

    Returns:
        The updated recipe dictionary if successful, None if not found
        or not authorized.

    Raises:
        ValueError: If the new YAML content is invalid or if the recipe
            is not editable (e.g., default recipes).
    """
    # Validate YAML content if provided
    if content is not None:
        # Get the existing recipe to know the recipe_type for validation
        existing = recipes_db.get_recipe(recipe_name)
        if existing is not None:
            _validate_skypilot_yaml(content, existing.recipe_type)

    recipe = recipes_db.update_recipe(
        recipe_name=recipe_name,
        user_id=user_id,
        user_name=user_name,
        description=description,
        content=content,
    )
    if recipe is None:
        return None
    return recipe.to_dict()


def delete_recipe(recipe_name: str, user_id: str) -> bool:
    """Delete a recipe.

    Only the owner can delete their recipe, and only if it's editable.

    Args:
        recipe_name: The recipe's unique name.
        user_id: ID of the user making the deletion (must be owner).

    Returns:
        True if deleted, False if not found or not authorized.

    Raises:
        ValueError: If the recipe is not editable (e.g., default recipes).
    """
    return recipes_db.delete_recipe(recipe_name, user_id)


def toggle_pin(recipe_name: str, pinned: bool) -> Optional[Dict[str, Any]]:
    """Toggle the pinned status of a recipe.

    Args:
        recipe_name: The recipe's unique name.
        pinned: New pinned status.

    Returns:
        The updated recipe dictionary if successful, None if not found.

    Raises:
        ValueError: If the recipe's pin status is not editable (e.g., default
        recipes).
    """
    recipe = recipes_db.toggle_pin(recipe_name, pinned)
    if recipe is None:
        return None
    return recipe.to_dict()
