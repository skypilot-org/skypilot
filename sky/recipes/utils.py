"""Utilities for recipes."""
import enum


class RecipeType(enum.Enum):
    """Type of recipe in the Recipe Hub."""
    CLUSTER = 'cluster'
    JOB = 'job'
    POOL = 'pool'
    VOLUME = 'volume'

    @classmethod
    def from_str(cls, value: str) -> 'RecipeType':
        """Convert string to RecipeType enum.

        Args:
            value: String value like 'cluster', 'job', 'pool', 'volume'.

        Returns:
            The corresponding RecipeType enum.

        Raises:
            ValueError: If the string is not a valid recipe type.
        """
        for recipe_type in cls:
            if recipe_type.value == value:
                return recipe_type
        valid_types = [rt.value for rt in cls]
        raise ValueError(f'Invalid recipe type: {value!r}. '
                         f'Must be one of: {", ".join(valid_types)}')
