"""Utilities for recipes."""
import enum
from typing import Union


class RecipeType(enum.Enum):
    """Type of recipe in the Recipe Hub."""
    CLUSTER = 'cluster'
    JOB = 'job'
    POOL = 'pool'
    VOLUME = 'volume'

    @classmethod
    def from_str(cls, value: str) -> Union['RecipeType', str]:
        """Convert string to RecipeType enum.

        For built-in types (cluster, job, pool, volume), returns the
        corresponding ``RecipeType`` enum member.  For plugin-registered
        types (i.e. types that have a ``RecipeValidator`` registered), the
        raw string is returned so it can be stored and round-tripped
        without the enum needing to know about every plugin type.

        Args:
            value: String value like 'cluster', 'job', 'pool', 'volume',
                or a plugin-registered type.

        Returns:
            The corresponding RecipeType enum or the raw string for
            plugin-registered types.

        Raises:
            ValueError: If the string is not a valid recipe type.
        """
        for recipe_type in cls:
            if recipe_type.value == value:
                return recipe_type
        # Accept plugin-registered types (stored as raw strings)
        # pylint: disable=import-outside-toplevel
        from sky.utils.plugin_extensions import RecipeValidator
        if RecipeValidator.has_validator(value):
            return value
        valid_types = [rt.value for rt in cls]
        raise ValueError(f'Invalid recipe type: {value!r}. '
                         f'Must be one of: {", ".join(valid_types)}')


def recipe_type_to_str(recipe_type: Union[RecipeType, str]) -> str:
    """Get the string value of a recipe type.

    Works for both built-in ``RecipeType`` enum members and raw strings
    returned by ``RecipeType.from_str()`` for plugin-registered types.
    """
    return recipe_type.value if isinstance(recipe_type,
                                           RecipeType) else recipe_type
