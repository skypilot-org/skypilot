"""Database for recipes.

This module provides a SQLAlchemy-backed database for storing recipes
used in the Recipes feature. The database stores recipes that can be
pinned globally, filtered by category/tags, and deployed as clusters, jobs,
pools, or volumes.
"""
import functools
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.ext import declarative

from sky import sky_logging
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
recipes_table = sqlalchemy.Table(
    RECIPES_TABLE,
    Base.metadata,
    sqlalchemy.Column('id', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('name', sqlalchemy.Text, nullable=False),
    sqlalchemy.Column('description', sqlalchemy.Text),
    sqlalchemy.Column('content', sqlalchemy.Text, nullable=False),
    sqlalchemy.Column('recipe_type', sqlalchemy.Text, nullable=False),
    sqlalchemy.Column('category', sqlalchemy.Text),
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
    sqlalchemy.Index('idx_recipe_category', 'category'),
    sqlalchemy.Index('idx_recipe_type', 'recipe_type'),
)

# Predefined categories that are always available
PREDEFINED_CATEGORIES = [
    {
        'name': 'training',
        'icon': '🎯'
    },
    {
        'name': 'inference',
        'icon': '🔮'
    },
    {
        'name': 'dev',
        'icon': '💻'
    },
    {
        'name': 'batch',
        'icon': '📦'
    },
    {
        'name': 'other',
        'icon': '📁'
    },
]

# Directory containing example YAML files
_EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), 'examples')

# Default templates: maps filename (without .yaml) to metadata
# Content is loaded from sky/yamls/examples/{filename}.yaml
DEFAULT_TEMPLATES: Dict[str, Dict[str, str]] = {
    'basic_cluster': {
        'name': 'Basic Cluster',
        'description': 'A simple cluster with GPU resources',
        'recipe_type': 'cluster',
        'category': 'dev',
    },
    'basic_managed_job': {
        'name': 'Basic Managed Job',
        'description': 'A simple managed job with automatic recovery',
        'recipe_type': 'job',
        'category': 'batch',
    },
    'basic_job_pool': {
        'name': 'Basic Job Pool',
        'description': 'A job pool for running multiple concurrent jobs',
        'recipe_type': 'pool',
        'category': 'batch',
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
            sqlalchemy.select(sqlalchemy.func.count()).select_from(
                recipes_table)).scalar()
        if count == 0:
            now = time.time()
            for filename, metadata in DEFAULT_TEMPLATES.items():
                try:
                    content = _load_example_content(filename)
                except FileNotFoundError:
                    logger.warning(f'Example file not found: {filename}.yaml')
                    continue

                recipe_id = str(uuid.uuid4())
                session.execute(recipes_table.insert().values(
                    id=recipe_id,
                    name=metadata['name'],
                    description=metadata['description'],
                    content=content,
                    recipe_type=metadata['recipe_type'],
                    category=metadata['category'],
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
    """Represents a YAML template."""

    def __init__(
        self,
        recipe_id: str,
        name: str,
        content: str,
        recipe_type: str,
        user_id: str,
        description: Optional[str] = None,
        category: Optional[str] = None,
        pinned: bool = False,
        user_name: Optional[str] = None,
        created_at: Optional[float] = None,
        updated_at: Optional[float] = None,
        updated_by_id: Optional[str] = None,
        updated_by_name: Optional[str] = None,
        is_editable: bool = True,
        is_pinnable: bool = True,
    ):
        self.id = recipe_id
        self.name = name
        self.description = description
        self.content = content
        self.recipe_type = recipe_type
        self.category = category
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
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'content': self.content,
            'recipe_type': self.recipe_type,
            'category': self.category,
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
            recipe_id=row.id,
            name=row.name,
            description=row.description,
            content=row.content,
            recipe_type=row.recipe_type,
            category=row.category,
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
    recipe_type: str,
    user_id: str,
    user_name: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
) -> Recipe:
    """Create a new recipe.

    Args:
        name: Display name for the recipe.
        content: The YAML content.
        recipe_type: Type of recipe ('cluster', 'job', 'pool', 'volume').
        user_id: ID of the user creating the recipe.
        user_name: Optional display name of the user.
        description: Optional description.
        category: Optional category for organization.

    Returns:
        The created YamlTemplate object.
    """
    assert _SQLALCHEMY_ENGINE is not None
    recipe_id = str(uuid.uuid4())
    now = time.time()

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.execute(recipes_table.insert().values(
            id=recipe_id,
            name=name,
            description=description,
            content=content,
            recipe_type=recipe_type,
            category=category,
            pinned=0,
            user_id=user_id,
            user_name=user_name,
            created_at=now,
            updated_at=now,
            is_editable=1,
            is_pinnable=1,
        ))
        session.commit()

    return Recipe(
        recipe_id=recipe_id,
        name=name,
        description=description,
        content=content,
        recipe_type=recipe_type,
        category=category,
        pinned=False,
        user_id=user_id,
        user_name=user_name,
        created_at=now,
        updated_at=now,
    )


@_init_db
def get_recipe(recipe_id: str) -> Optional[Recipe]:
    """Get a YAML template by ID.

    Args:
        recipe_id: The recipe's unique ID.

    Returns:
        The YamlTemplate if found, None otherwise.
    """
    assert _SQLALCHEMY_ENGINE is not None

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(recipes_table).where(
                recipes_table.c.id == recipe_id))
        row = result.fetchone()
        if row is None:
            return None
        return Recipe.from_row(row)


@_init_db
def list_recipes(
    user_id: Optional[str] = None,
    pinned_only: bool = False,
    my_recipes_only: bool = False,
    category: Optional[str] = None,
    recipe_type: Optional[str] = None,
) -> List[Recipe]:
    """List recipes with optional filters.

    The default behavior returns all recipes. The frontend handles
    categorization into "My Recipes", "All Recipes", and "Pinned" sections.

    Args:
        user_id: Filter to recipes owned by this user (for my_recipes_only).
        pinned_only: If True, only return pinned templates.
        my_recipes_only: If True, only return recipes owned by user_id.
        category: Filter by category.
        recipe_type: Filter by type ('cluster', 'job', 'serve', 'pool').

    Returns:
        List of matching YamlTemplate objects.
    """
    assert _SQLALCHEMY_ENGINE is not None

    query = sqlalchemy.select(recipes_table)

    if pinned_only:
        query = query.where(recipes_table.c.pinned == 1)
    elif my_recipes_only and user_id:
        query = query.where(recipes_table.c.user_id == user_id)

    if category:
        query = query.where(recipes_table.c.category == category)

    if recipe_type:
        query = query.where(recipes_table.c.recipe_type == recipe_type)

    query = query.order_by(recipes_table.c.pinned.desc(),
                           recipes_table.c.updated_at.desc())

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(query)
        rows = result.fetchall()

    return [Recipe.from_row(row) for row in rows]


@_init_db
def update_recipe(
    recipe_id: str,
    user_id: str,
    user_name: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    content: Optional[str] = None,
    category: Optional[str] = None,
) -> Optional[Recipe]:
    """Update a recipe.

    Anyone can update a recipe, but only if it's editable.

    Args:
        recipe_id: The recipe's unique ID.
        user_id: ID of the user making the update.
        user_name: Name of the user making the update.
        name: New name (if updating).
        description: New description (if updating).
        content: New YAML content (if updating).
        category: New category (if updating).

    Returns:
        The updated Recipe if successful, None if not found
        or not authorized.

    Raises:
        ValueError: If the recipe is not editable (e.g., default recipes).
    """
    assert _SQLALCHEMY_ENGINE is not None

    # First check ownership and editability
    recipe = get_recipe(recipe_id)
    if recipe is None:
        return None
    if not recipe.is_editable:
        raise ValueError('This recipe cannot be edited')

    # TODO(lloyd): We might want to change this in the future to change who is
    # allowed to update a recipe.

    updates: Dict[str, Any] = {}
    if name is not None:
        updates['name'] = name
    if description is not None:
        updates['description'] = description
    if content is not None:
        updates['content'] = content
    if category is not None:
        updates['category'] = category

    if not updates:
        return recipe

    updates['updated_at'] = time.time()
    updates['updated_by_id'] = user_id
    updates['updated_by_name'] = user_name

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.execute(recipes_table.update().where(
            recipes_table.c.id == recipe_id).values(**updates))
        session.commit()

    return get_recipe(recipe_id)


@_init_db
def delete_recipe(recipe_id: str, user_id: str) -> bool:
    """Delete a recipe.

    Only the owner can delete a recipe, and only if it's editable.

    Args:
        recipe_id: The recipe's unique ID.
        user_id: ID of the user making the deletion (must be owner).

    Returns:
        True if deleted, False if not found or not authorized.

    Raises:
        ValueError: If the recipe is not editable (e.g., default recipes).
    """
    assert _SQLALCHEMY_ENGINE is not None

    # First check ownership and editability
    recipe = get_recipe(recipe_id)
    if recipe is None:
        return False
    if not recipe.is_editable:
        raise ValueError('This recipe cannot be deleted')
    if recipe.user_id != user_id:
        return False

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.execute(recipes_table.delete().where(
            recipes_table.c.id == recipe_id))
        session.commit()

    return True


@_init_db
def toggle_pin(recipe_id: str, pinned: bool) -> Optional[Recipe]:
    """Toggle the pinned status of a recipe.

    This is an admin-only operation (authorization should be checked
    by the caller). Recipes must be pinnable to be pinned/unpinned.

    Args:
        recipe_id: The recipe's unique ID.
        pinned: New pinned status.

    Returns:
        The updated Recipe if successful, None if not found.

    Raises:
        ValueError: If the recipe is not pinnable (e.g., default recipes).
    """
    assert _SQLALCHEMY_ENGINE is not None

    recipe = get_recipe(recipe_id)
    if recipe is None:
        return None
    if not recipe.is_pinnable:
        raise ValueError('This recipe cannot be pinned or unpinned')

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.execute(recipes_table.update().where(
            recipes_table.c.id == recipe_id).values(
                pinned=1 if pinned else 0, updated_at=time.time()))
        session.commit()

    return get_recipe(recipe_id)


def get_predefined_category_names() -> List[str]:
    """Get list of predefined category names.

    Returns:
        List of predefined category names.
    """
    return [cat['name'] for cat in PREDEFINED_CATEGORIES]


@_init_db
def get_custom_categories() -> List[str]:
    """Get custom categories from recipes.

    Returns DISTINCT category names that are not predefined.

    Returns:
        List of custom category names (sorted).
    """
    assert _SQLALCHEMY_ENGINE is not None
    predefined_names = set(get_predefined_category_names())

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(recipes_table.c.category).distinct().where(
                recipes_table.c.category.isnot(None)).order_by(
                    recipes_table.c.category))
        rows = result.fetchall()

    # Filter out predefined categories
    custom = [
        row[0] for row in rows if row[0] and row[0] not in predefined_names
    ]
    return sorted(custom)


@_init_db
def get_all_categories() -> List[str]:
    """Get all categories (predefined + custom from recipes).

    Returns:
        List of all category names (sorted).
    """
    predefined = get_predefined_category_names()
    custom = get_custom_categories()
    return predefined + custom
