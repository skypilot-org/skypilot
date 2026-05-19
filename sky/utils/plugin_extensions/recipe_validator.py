"""Recipe validator extension point for plugins.

Allows plugins to register custom validation functions for plugin-specific
recipe types. Core SkyPilot calls ``RecipeValidator.validate()`` during
recipe create/update; if no validator is registered for the recipe type,
validation falls back to the built-in SkyPilot schema checks.

Example usage in a plugin::

    from sky.utils.plugin_extensions import RecipeValidator

    RecipeValidator.register(
        recipe_type='custom',
        validate_fn=my_validate_fn,
    )

Example usage in core SkyPilot::

    from sky.utils.plugin_extensions import RecipeValidator

    # Validate recipe content (raises ValueError on failure)
    RecipeValidator.validate('custom', yaml_content)
"""
from typing import Callable, Dict

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Validator signature: takes YAML content string, raises ValueError if invalid.
ValidateFn = Callable[[str], None]


class RecipeValidator:
    """Registry for plugin recipe-type validators."""

    _validators: Dict[str, ValidateFn] = {}

    @classmethod
    def register(cls, recipe_type: str, validate_fn: ValidateFn) -> None:
        """Register a validator for a recipe type.

        Args:
            recipe_type: Recipe type string identifying the plugin-provided
                recipe type.
            validate_fn: Callable that takes a YAML content string and
                raises ``ValueError`` if the content is invalid.
        """
        cls._validators[recipe_type] = validate_fn
        logger.debug(f'Registered recipe validator for type: {recipe_type}')

    @classmethod
    def has_validator(cls, recipe_type: str) -> bool:
        """Check if a validator is registered for the recipe type."""
        return recipe_type in cls._validators

    @classmethod
    def validate(cls, recipe_type: str, content: str) -> None:
        """Validate recipe content using the registered validator.

        Args:
            recipe_type: The recipe type string.
            content: The YAML content to validate.

        Raises:
            ValueError: If validation fails.
        """
        validator = cls._validators.get(recipe_type)
        if validator is None:
            return
        validator(content)
