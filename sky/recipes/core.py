"""Core business logic for Recipe Hub.

This module provides the main entry points for recipe operations,
including CRUD operations and deployment functionality.
"""
from typing import Any, Dict, List, Optional, Tuple

import yaml

from sky import sky_logging
from sky import task as task_lib
from sky.recipes import db as recipes_db

logger = sky_logging.init_logger(__name__)


def _validate_skypilot_yaml(content: str, recipe_type: str) -> None:
    """Validate YAML content against SkyPilot schema.

    Args:
        content: The YAML content string.
        recipe_type: Type of recipe ('cluster', 'job', 'pool', 'volume').

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

        # Validate based on type
        if recipe_type == 'volume':
            # TODO(lloyd: add volume validation)
            pass
        else:
            if recipe_type == 'pool':
                # Pool YAMLs should have a 'pool' section
                if 'pool' not in config:
                    raise ValueError('Pool YAML must contain a \'pool\''
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


def get_recipe_content(recipe_id: str) -> Tuple[str, str]:
    """Get recipe content and type by ID.

    This function is used by the CLI to fetch a recipe from the Hub
    when launching with --recipe-id.

    Args:
        recipe_id: The recipe's unique ID.

    Returns:
        Tuple of (recipe_content, recipe_type).

    Raises:
        ValueError: If recipe not found.
    """
    template = recipes_db.get_recipe(recipe_id)
    if template is None:
        raise ValueError(f'Recipe not found: {recipe_id}')
    return (template.content, template.recipe_type)


def list_recipes(
    user_id: Optional[str] = None,
    category: Optional[str] = None,
    pinned_only: bool = False,
    my_recipes_only: bool = False,
    recipe_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List recipes with optional filters.

    By default, returns all pinned recipes plus recipes owned by the user.

    Args:
        user_id: The current user's ID (needed to see own recipes).
        category: Filter by category.
        pinned_only: If True, only return pinned recipes.
        my_recipes_only: If True, only return recipes owned by user_id.
        recipe_type: Filter by type ('cluster' or 'job' or 'pool' or 'volume').

    Returns:
        List of recipe dictionaries.
    """
    recipes = recipes_db.list_recipes(
        user_id=user_id,
        pinned_only=pinned_only,
        my_recipes_only=my_recipes_only,
        category=category,
        recipe_type=recipe_type,
    )
    return [r.to_dict() for r in recipes]


def get_recipe(recipe_id: str) -> Optional[Dict[str, Any]]:
    """Get a single YAML template by ID.

    Args:
        template_id: The template's unique ID.

    Returns:
        Template dictionary if found, None otherwise.
    """
    recipe = recipes_db.get_recipe(recipe_id)
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
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new recipe.

    Args:
        name: Display name for the recipe.
        content: The YAML content.
        recipe_type: Type of recipe ('cluster' or 'job').
        user_id: ID of the user creating the recipe.
        user_name: Optional display name of the user.
        description: Optional description.
        category: Optional category for organization.

    Returns:
        The created recipe as a dictionary.

    Raises:
        ValueError: If the YAML content is invalid or recipe_type is invalid.
    """
    # Validate recipe_type
    valid_types = ('cluster', 'job', 'pool', 'volume')
    if recipe_type not in valid_types:
        raise ValueError(
            f'recipe_type must be one of {valid_types}, got {recipe_type!r}')

    # Validate content against SkyPilot schema
    _validate_skypilot_yaml(content, recipe_type)

    recipe = recipes_db.create_recipe(
        name=name,
        content=content,
        recipe_type=recipe_type,
        user_id=user_id,
        user_name=user_name,
        description=description,
        category=category,
    )
    return recipe.to_dict()


def update_recipe(
    recipe_id: str,
    user_id: str,
    user_name: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    content: Optional[str] = None,
    category: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update a recipe.

    Only the owner can update their recipe, and only if it's editable.

    Args:
        recipe_id: The recipe's unique ID.
        user_id: ID of the user making the update (must be owner).
        user_name: Name of the user making the update.
        name: New name (if updating).
        description: New description (if updating).
        content: New YAML content (if updating).
        category: New category (if updating).

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
        existing = recipes_db.get_recipe(recipe_id)
        if existing is not None:
            _validate_skypilot_yaml(content, existing.recipe_type)

    recipe = recipes_db.update_recipe(
        recipe_id=recipe_id,
        user_id=user_id,
        user_name=user_name,
        name=name,
        description=description,
        content=content,
        category=category,
    )
    if recipe is None:
        return None
    return recipe.to_dict()


def delete_recipe(recipe_id: str, user_id: str) -> bool:
    """Delete a recipe.

    Only the owner can delete their recipe, and only if it's editable.

    Args:
        recipe_id: The recipe's unique ID.
        user_id: ID of the user making the deletion (must be owner).

    Returns:
        True if deleted, False if not found or not authorized.

    Raises:
        ValueError: If the recipe is not editable (e.g., default recipes).
    """
    return recipes_db.delete_recipe(recipe_id, user_id)


def toggle_pin(recipe_id: str, pinned: bool) -> Optional[Dict[str, Any]]:
    """Toggle the pinned status of a recipe.

    This is an admin-only operation - authorization should be checked
    by the API layer before calling this function.

    Args:
        recipe_id: The recipe's unique ID.
        pinned: New pinned status.

    Returns:
        The updated recipe dictionary if successful, None if not found.

    Raises:
        ValueError: If the recipe is not pinnable (e.g., default recipes).
    """
    recipe = recipes_db.toggle_pin(recipe_id, pinned)
    if recipe is None:
        return None
    return recipe.to_dict()


def get_categories() -> Dict[str, Any]:
    """Get all available categories.

    Returns predefined categories and custom categories derived from templates.

    Returns:
        Dictionary with 'predefined', 'custom', and 'all' category lists.
        Predefined categories have name and icon, custom are just names.
    """
    predefined = recipes_db.PREDEFINED_CATEGORIES  # List of {name, icon} dicts
    custom = recipes_db.get_custom_categories()  # List of category names
    all_names = recipes_db.get_all_categories()  # Combined list of names
    return {
        'predefined': predefined,
        'custom': custom,
        'all': all_names,
    }


def list_all_categories() -> List[str]:
    """List all categories (predefined and custom).

    Returns:
        List of category names.
    """
    return recipes_db.get_all_categories()
