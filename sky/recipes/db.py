"""Database for recipes.

This module provides a SQLAlchemy-backed database for storing recipes
used in the Recipes feature. The database stores recipes that can be
pinned globally, filtered by category/tags, and deployed as clusters, jobs,
pools, or volumes.
"""
import functools
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.ext import declarative

from sky import exceptions
from sky import sky_logging
from sky.recipes.utils import RecipeType
from sky.utils import common_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils

logger = sky_logging.init_logger(__name__)

# SQLAlchemy engine
_SQLALCHEMY_ENGINE: Optional[sqlalchemy.engine.Engine] = None
_SQLALCHEMY_ENGINE_LOCK = threading.Lock()

# SQLAlchemy Base
Base = declarative.declarative_base()

# Table name
RECIPES_TABLE = 'recipes'

# SQLAlchemy table definition
# Note: 'name' is the primary key and unique identifier for recipes
recipes_table = sqlalchemy.Table(
    RECIPES_TABLE,
    Base.metadata,
    sqlalchemy.Column('name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('description', sqlalchemy.Text),
    sqlalchemy.Column('content', sqlalchemy.Text, nullable=False),
    sqlalchemy.Column('recipe_type', sqlalchemy.Text, nullable=False),
    sqlalchemy.Column('pinned', sqlalchemy.Integer, default=0),
    sqlalchemy.Column('user_id', sqlalchemy.Text, nullable=False),
    sqlalchemy.Column('user_name', sqlalchemy.Text),
    sqlalchemy.Column('created_at', sqlalchemy.Float),
    sqlalchemy.Column('updated_at', sqlalchemy.Float),
    sqlalchemy.Column('updated_by_id', sqlalchemy.Text),
    sqlalchemy.Column('updated_by_name', sqlalchemy.Text),
    sqlalchemy.Column('is_editable', sqlalchemy.Integer, default=1),
    sqlalchemy.Column('is_pinnable', sqlalchemy.Integer, default=1),
    sqlalchemy.Index('idx_recipe_user_id', 'user_id'),
    sqlalchemy.Index('idx_recipe_pinned', 'pinned'),
    sqlalchemy.Index('idx_recipe_type', 'recipe_type'),
)

# Directory containing example YAML files
_EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), 'examples')

# Default templates: maps filename (without .yaml) to metadata
# Content is loaded from sky/recipes/examples/{filename}.yaml
# Note: Recipe names must use letters, numbers, and dashes only
DEFAULT_TEMPLATES: Dict[str, Dict[str, str]] = {
    'basic_cluster': {
        'name': 'basic-cluster',
        'description': 'A simple cluster with GPU resources',
        'recipe_type': 'cluster',
    },
    'basic_managed_job': {
        'name': 'basic-managed-job',
        'description': 'A simple job with recovery',
        'recipe_type': 'job',
    },
    'basic_pool': {
        'name': 'basic-pool',
        'description': 'A pool for concurrent jobs',
        'recipe_type': 'pool',
    },
    'basic_volume': {
        'name': 'basic-volume',
        'description': 'A starting point for a k8s volume',
        'recipe_type': 'volume',
    },
}


def _load_example_content(filename: str) -> str:
    """Load YAML content from an example file.

    Args:
        filename: The filename without .yaml extension.

    Returns:
        The file content as a string.

    Raises:
        FileNotFoundError: If the example file doesn't exist.
    """
    filepath = os.path.join(_EXAMPLES_DIR, f'{filename}.yaml')
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()


def _create_table(engine: sqlalchemy.engine.Engine) -> None:
    """Create tables and run migrations."""
    # Enable WAL mode for SQLite to avoid locking issues
    if (engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value and
            not common_utils.is_wsl()):
        try:
            with orm.Session(engine) as session:
                session.execute(sqlalchemy.text('PRAGMA journal_mode=WAL'))
                session.commit()
        except sqlalchemy.exc.OperationalError as e:
            if 'database is locked' not in str(e):
                raise

    # Run migrations
    migration_utils.safe_alembic_upgrade(engine,
                                         migration_utils.RECIPES_DB_NAME,
                                         migration_utils.RECIPES_VERSION)


def _insert_default_templates(engine: sqlalchemy.engine.Engine) -> None:
    """Insert default templates if the table is empty.

    Loads recipe content from example files in sky/recipes/examples/ and
    populates the database with the default templates.
    """
    with orm.Session(engine) as session:
        # Check if table is empty
        # pylint: disable=not-callable
        count = session.execute(
            sqlalchemy.select(
                sqlalchemy.func.count()).select_from(recipes_table)).scalar()
        if count == 0:
            now = time.time()
            for filename, metadata in DEFAULT_TEMPLATES.items():
                try:
                    content = _load_example_content(filename)
                except FileNotFoundError:
                    logger.warning(f'Example file not found: {filename}.yaml')
                    continue

                session.execute(recipes_table.insert().values(
                    name=metadata['name'],
                    description=metadata['description'],
                    content=content,
                    recipe_type=metadata['recipe_type'],
                    pinned=1,
                    user_id='system',
                    user_name='SkyPilot',
                    created_at=now,
                    updated_at=now,
                    is_editable=0,
                    is_pinnable=0,
                ))
            session.commit()


def initialize_and_get_db() -> sqlalchemy.engine.Engine:
    """Initialize and return the database engine."""
    global _SQLALCHEMY_ENGINE

    if _SQLALCHEMY_ENGINE is not None:
        return _SQLALCHEMY_ENGINE

    with _SQLALCHEMY_ENGINE_LOCK:
        if _SQLALCHEMY_ENGINE is not None:
            return _SQLALCHEMY_ENGINE

        # Get an engine to the db
        engine = db_utils.get_engine('recipes')

        # Run migrations
        _create_table(engine)

        # Insert default templates after migrations
        _insert_default_templates(engine)

        _SQLALCHEMY_ENGINE = engine
        return _SQLALCHEMY_ENGINE


def _init_db(func: Callable) -> Callable:
    """Decorator to initialize database connection before function call."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        initialize_and_get_db()
        return func(*args, **kwargs)

    return wrapper


# =============================================================================
# Data classes
# =============================================================================


class Recipe:
    """Represents a YAML template.

    Recipes are uniquely identified by their name, which must follow the
    naming convention (letters, numbers, and dashes only).
    """

    def __init__(
        self,
        name: str,
        content: str,
        recipe_type: RecipeType,
        user_id: str,
        description: Optional[str] = None,
        pinned: bool = False,
        user_name: Optional[str] = None,
        created_at: Optional[float] = None,
        updated_at: Optional[float] = None,
        updated_by_id: Optional[str] = None,
        updated_by_name: Optional[str] = None,
        is_editable: bool = True,
        is_pinnable: bool = True,
    ):
        self.name = name
        self.description = description
        self.content = content
        self.recipe_type = recipe_type
        self.pinned = pinned
        self.user_id = user_id
        self.user_name = user_name
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()
        self.updated_by_id = updated_by_id
        self.updated_by_name = updated_by_name
        self.is_editable = is_editable
        self.is_pinnable = is_pinnable

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            'name': self.name,
            'description': self.description,
            'content': self.content,
            'recipe_type': self.recipe_type.value,
            'pinned': self.pinned,
            'user_id': self.user_id,
            'user_name': self.user_name,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'updated_by_id': self.updated_by_id,
            'updated_by_name': self.updated_by_name,
            'is_editable': self.is_editable,
            'is_pinnable': self.is_pinnable,
        }

    @classmethod
    def from_row(cls, row) -> 'Recipe':
        """Create from database row."""
        return cls(
            name=row.name,
            description=row.description,
            content=row.content,
            recipe_type=RecipeType.from_str(row.recipe_type),
            pinned=bool(row.pinned),
            user_id=row.user_id,
            user_name=row.user_name,
            created_at=row.created_at,
            updated_at=row.updated_at,
            updated_by_id=row.updated_by_id,
            updated_by_name=row.updated_by_name,
            is_editable=bool(row.is_editable),
            is_pinnable=bool(row.is_pinnable),
        )


# =============================================================================
# Database operations
# =============================================================================


@_init_db
def create_recipe(
    name: str,
    content: str,
    recipe_type: RecipeType,
    user_id: str,
    user_name: Optional[str] = None,
    description: Optional[str] = None,
) -> Recipe:
    """Create a new recipe.

    Args:
        name: Unique name for the recipe. Must contain only letters, numbers,
            and dashes.
        content: The YAML content.
        recipe_type: Type of recipe.
        user_id: ID of the user creating the recipe.
        user_name: Optional display name of the user.
        description: Optional description.

    Returns:
        The created Recipe object.

    Raises:
        exceptions.InvalidRecipeNameError: If the name format is invalid.
        exceptions.RecipeAlreadyExistsError: If a recipe with this name exists.
    """
    assert _SQLALCHEMY_ENGINE is not None

    # Validate name format
    common_utils.check_recipe_name_is_valid(name)

    now = time.time()

    # Use atomic insert - the primary key constraint on 'name' ensures
    # uniqueness. Catching IntegrityError avoids race conditions between
    # check and insert, and works for both SQLite and PostgreSQL.
    try:
        with orm.Session(_SQLALCHEMY_ENGINE) as session:
            session.execute(recipes_table.insert().values(
                name=name,
                description=description,
                content=content,
                recipe_type=recipe_type.value,
                pinned=0,
                user_id=user_id,
                user_name=user_name,
                created_at=now,
                updated_at=now,
                is_editable=1,
                is_pinnable=1,
            ))
            session.commit()
    except sqlalchemy.exc.IntegrityError as e:
        raise exceptions.RecipeAlreadyExistsError(
            f'A recipe with name "{name}" already exists') from e

    return Recipe(
        name=name,
        description=description,
        content=content,
        recipe_type=recipe_type,
        pinned=False,
        user_id=user_id,
        user_name=user_name,
        created_at=now,
        updated_at=now,
    )


@_init_db
def get_recipe(recipe_name: str) -> Optional[Recipe]:
    """Get a recipe by name.

    Args:
        recipe_name: The recipe's unique name.

    Returns:
        The Recipe if found, None otherwise.
    """
    assert _SQLALCHEMY_ENGINE is not None

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(recipes_table).where(
                recipes_table.c.name == recipe_name))
        row = result.fetchone()
        if row is None:
            return None
        return Recipe.from_row(row)


@_init_db
def list_recipes(
    user_id: Optional[str] = None,
    pinned_only: bool = False,
    my_recipes_only: bool = False,
    recipe_type: Optional[RecipeType] = None,
) -> List[Recipe]:
    """List recipes with optional filters.

    The default behavior returns all recipes. The frontend handles
    categorization into "My Recipes", "All Recipes", and "Pinned" sections.

    Args:
        user_id: Filter to recipes owned by this user (for my_recipes_only).
        pinned_only: If True, only return pinned templates.
        my_recipes_only: If True, only return recipes owned by user_id.
        recipe_type: Filter by type.

    Returns:
        List of matching YamlTemplate objects.
    """
    assert _SQLALCHEMY_ENGINE is not None

    query = sqlalchemy.select(recipes_table)

    if pinned_only:
        query = query.where(recipes_table.c.pinned == 1)
    elif my_recipes_only and user_id:
        query = query.where(recipes_table.c.user_id == user_id)

    if recipe_type:
        query = query.where(recipes_table.c.recipe_type == recipe_type.value)

    query = query.order_by(recipes_table.c.pinned.desc(),
                           recipes_table.c.name.asc())

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(query)
        rows = result.fetchall()

    return [Recipe.from_row(row) for row in rows]


@_init_db
def update_recipe(
    recipe_name: str,
    user_id: str,
    user_name: Optional[str] = None,
    description: Optional[str] = None,
    content: Optional[str] = None,
) -> Optional[Recipe]:
    """Update a recipe.

    Anyone can update a recipe, but only if it's editable.
    Note: Recipe names cannot be changed as they are the primary identifier.

    Args:
        recipe_name: The recipe's unique name.
        user_id: ID of the user making the update.
        user_name: Name of the user making the update.
        description: New description (if updating).
        content: New YAML content (if updating).

    Returns:
        The updated Recipe if successful.

    Raises:
        ValueError: If the recipe is not found or not editable.
    """
    assert _SQLALCHEMY_ENGINE is not None

    # TODO(lloyd): We might want to change this in the future to change who is
    # allowed to update a recipe.

    updates: Dict[str, Any] = {}
    if description is not None:
        updates['description'] = description
    if content is not None:
        updates['content'] = content

    if not updates:
        # No updates requested, just return current state
        return get_recipe(recipe_name)

    updates['updated_at'] = time.time()
    updates['updated_by_id'] = user_id
    updates['updated_by_name'] = user_name

    # Atomic update with editability check in WHERE clause.
    # This avoids race conditions between check and update, and works
    # for both SQLite and PostgreSQL.
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(recipes_table.update().where(
            recipes_table.c.name == recipe_name).where(
                recipes_table.c.is_editable == 1).values(**updates))
        session.commit()

        if result.rowcount == 0:
            # No rows updated - either recipe not found or not editable.
            # Query to determine which case.
            recipe = get_recipe(recipe_name)
            if recipe is None:
                raise ValueError(f'Recipe {recipe_name} not found')
            # Recipe exists but wasn't updated -> not editable
            raise ValueError(f'Recipe {recipe_name} is not editable')

    return get_recipe(recipe_name)


@_init_db
def delete_recipe(recipe_name: str, user_id: str) -> bool:
    """Delete a recipe.

    Only the owner can delete a recipe, and only if it's editable.

    Args:
        recipe_name: The recipe's unique name.
        user_id: ID of the user making the deletion (must be owner).

    Returns:
        True if deleted, False if not found or not authorized.

    Raises:
        ValueError: If the recipe is not editable (e.g., default recipes).
    """
    assert _SQLALCHEMY_ENGINE is not None

    # Atomic delete with ownership and editability checks in WHERE clause.
    # This avoids race conditions between check and delete, and works
    # for both SQLite and PostgreSQL.
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(recipes_table.delete().where(
            recipes_table.c.name == recipe_name).where(
                recipes_table.c.is_editable == 1).where(
                    recipes_table.c.user_id == user_id))
        session.commit()

        if result.rowcount > 0:
            return True

        # No rows deleted - determine why (not found, not editable, or
        # not owner).
        recipe = get_recipe(recipe_name)
        if recipe is None:
            return False
        if not recipe.is_editable:
            raise ValueError('This recipe cannot be deleted')
        # Recipe exists and is editable but wasn't deleted -> not owner
        return False


@_init_db
def toggle_pin(recipe_name: str, pinned: bool) -> Optional[Recipe]:
    """Toggle the pinned status of a recipe.

    This is an admin-only operation (authorization should be checked
    by the caller). Recipes must be pinnable to be pinned/unpinned.

    Args:
        recipe_name: The recipe's unique name.
        pinned: New pinned status.

    Returns:
        The updated Recipe if successful, None if not found.

    Raises:
        ValueError: If the recipe is not pinnable (e.g., default recipes).
    """
    assert _SQLALCHEMY_ENGINE is not None

    # Atomic update with pinnable check in WHERE clause.
    # This avoids race conditions between check and update, and works
    # for both SQLite and PostgreSQL.
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(recipes_table.update().where(
            recipes_table.c.name == recipe_name).where(
                recipes_table.c.is_pinnable == 1).values(
                    pinned=1 if pinned else 0, updated_at=time.time()))
        session.commit()

        if result.rowcount > 0:
            return get_recipe(recipe_name)

        # No rows updated - either not found or not pinnable.
        recipe = get_recipe(recipe_name)
        if recipe is None:
            return None
        # Recipe exists but wasn't updated -> not pinnable
        raise ValueError('This recipe cannot be pinned or unpinned')
