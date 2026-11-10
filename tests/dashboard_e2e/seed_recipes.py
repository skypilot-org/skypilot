"""Seed recipes straight into the e2e server's recipes DB.

The test process shares SKY_RUNTIME_DIR with the server, so these helpers
write the same recipes DB the server reads. Recipes a test creates are named
with its ``uid`` prefix; a leftover check makes exclusive tests wait until
they are gone.
"""
from typing import Dict, List, Optional

from framework import seed
import sqlalchemy
from sqlalchemy import orm

from sky.recipes import db as recipes_db

JOB_YAML = """name: e2e-job
resources:
  cpus: 1
run: echo e2e
"""


def _engine() -> sqlalchemy.engine.Engine:
    return recipes_db._db_manager.get_engine()  # pylint: disable=protected-access


def recipe(name: str,
           user_id: str,
           user_name: str,
           recipe_type: str = 'job',
           content: str = JOB_YAML,
           description: Optional[str] = None,
           pinned: bool = False) -> str:
    """Insert one user-owned recipe and return its name."""
    recipes_db.create_recipe(name=name,
                             content=content,
                             recipe_type=recipe_type,
                             user_id=user_id,
                             user_name=user_name,
                             description=description)
    if pinned:
        recipes_db.toggle_pin(name, True)
    return name


def get(name: str) -> Optional[Dict]:
    r = recipes_db.get_recipe(name)
    return None if r is None else r.to_dict()


def delete(name: str) -> None:
    t = recipes_db.recipes_table
    with orm.Session(_engine()) as session:
        session.execute(sqlalchemy.delete(t).where(t.c.name == name))
        session.commit()


def count_recipes() -> int:
    t = recipes_db.recipes_table
    with orm.Session(_engine()) as session:
        return session.execute(
            sqlalchemy.select(sqlalchemy.func.count()).select_from(t)  # pylint: disable=not-callable
        ).scalar_one()


def pinned_names() -> List[str]:
    t = recipes_db.recipes_table
    with orm.Session(_engine()) as session:
        return list(
            session.execute(sqlalchemy.select(
                t.c.name).where(t.c.pinned == 1)).scalars().all())


def default_names() -> List[str]:
    """Recipes the server ships with (not editable, owned by the system)."""
    t = recipes_db.recipes_table
    with orm.Session(_engine()) as session:
        return sorted(
            session.execute(
                sqlalchemy.select(t.c.name).where(t.c.is_editable == 0).where(
                    t.c.user_id == 'system')).scalars().all())


def _leftover_recipes() -> List[str]:
    t = recipes_db.recipes_table
    with orm.Session(_engine()) as session:
        names = session.execute(sqlalchemy.select(t.c.name)).scalars().all()
    return [f'recipe {n}' for n in names if seed.is_test_name(n)]


seed.register_leftover_check(_leftover_recipes)
