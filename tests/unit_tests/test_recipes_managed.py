"""Unit tests for externally managed recipes in the recipes DB."""
# Pytest fixture name collides with pylint's "private name" rule.
# pylint: disable=redefined-outer-name,unused-argument,protected-access
import textwrap

import pytest
import sqlalchemy

from sky import exceptions
from sky.recipes import core as recipes_core
from sky.recipes import db as recipes_db
from sky.recipes.utils import RecipeType

_OWNER = 'git:42'
_SOURCE = {
    'kind': 'git',
    'url': 'https://github.com/example-org/recipes',
    'ref': 'main',
    'sha': 'a1b2c3d',
    'path': 'clusters/basic.yaml',
}
_CONTENT = textwrap.dedent("""
    resources:
      cpus: 2
    run: echo hello
    """).strip()


@pytest.fixture
def recipes_engine(tmp_path, monkeypatch):
    engine = sqlalchemy.create_engine(f'sqlite:///{tmp_path}/recipes.db')
    recipes_db.Base.metadata.create_all(engine)
    monkeypatch.setattr(recipes_db._db_manager, '_engine', engine)
    yield engine


def _upsert(name='synced-cluster', content=_CONTENT, source=None, **kwargs):
    return recipes_db.upsert_managed_recipe(
        name=name,
        content=content,
        recipe_type=RecipeType.CLUSTER,
        owner_id=_OWNER,
        owner_name='example-org/recipes',
        source=source or _SOURCE,
        **kwargs,
    )


def test_insert_is_pinned_read_only_and_records_source(recipes_engine):
    recipe = _upsert(description='Synced', updated_by_name='mchen')
    assert recipe.pinned
    assert not recipe.is_editable
    assert recipe.is_pinnable
    assert recipe.user_id == _OWNER
    assert recipe.updated_by_name == 'mchen'
    assert recipe.to_dict()['source'] == _SOURCE


def test_update_preserves_unpin(recipes_engine):
    _upsert()
    recipes_db.toggle_pin('synced-cluster', False)
    updated = _upsert(content=_CONTENT + '\nnum_nodes: 2',
                      source={
                          **_SOURCE, 'sha': 'f00ba12'
                      },
                      updated_by_name='priya')
    assert not updated.pinned
    assert 'num_nodes: 2' in updated.content
    assert updated.source['sha'] == 'f00ba12'
    assert updated.updated_by_name == 'priya'


def test_source_only_change_keeps_updated_at(recipes_engine):
    first = _upsert(updated_by_name='mchen')
    second = _upsert(source={
        **_SOURCE, 'sha': 'f00ba12'
    },
                     updated_by_name='someone-else')
    assert second.updated_at == first.updated_at
    assert second.updated_by_name == 'mchen'
    assert second.source['sha'] == 'f00ba12'


def test_name_owned_by_user_is_not_overwritten(recipes_engine):
    recipes_db.create_recipe(name='taken',
                             content=_CONTENT,
                             recipe_type=RecipeType.CLUSTER,
                             user_id='user-1')
    with pytest.raises(exceptions.RecipeAlreadyExistsError):
        _upsert(name='taken')
    assert recipes_db.get_recipe('taken').user_id == 'user-1'


def test_name_owned_by_other_manager_is_not_overwritten(recipes_engine):
    _upsert()
    with pytest.raises(exceptions.RecipeAlreadyExistsError):
        recipes_db.upsert_managed_recipe(name='synced-cluster',
                                         content=_CONTENT,
                                         recipe_type=RecipeType.CLUSTER,
                                         owner_id='git:99',
                                         owner_name='other/repo',
                                         source=_SOURCE)


def test_regular_update_and_delete_are_rejected(recipes_engine):
    _upsert()
    with pytest.raises(ValueError, match='not editable'):
        recipes_db.update_recipe('synced-cluster',
                                 user_id='user-1',
                                 content=_CONTENT)
    with pytest.raises(ValueError, match='cannot be deleted'):
        recipes_db.delete_recipe('synced-cluster', user_id=_OWNER)


def test_delete_managed_recipe_only_deletes_owned(recipes_engine):
    _upsert()
    recipes_db.create_recipe(name='mine',
                             content=_CONTENT,
                             recipe_type=RecipeType.CLUSTER,
                             user_id='user-1')
    assert not recipes_db.delete_managed_recipe('mine', _OWNER)
    assert recipes_db.get_recipe('mine') is not None
    assert recipes_db.delete_managed_recipe('synced-cluster', _OWNER)
    assert recipes_db.get_recipe('synced-cluster') is None


def test_invalid_name_rejected(recipes_engine):
    with pytest.raises(exceptions.InvalidRecipeNameError):
        _upsert(name='bad_name')


def test_regular_recipe_has_no_source(recipes_engine):
    recipe = recipes_db.create_recipe(name='plain',
                                      content=_CONTENT,
                                      recipe_type=RecipeType.CLUSTER,
                                      user_id='user-1')
    assert recipes_db.get_recipe(recipe.name).to_dict()['source'] is None


def test_validate_recipe_content_accepts_type_string():
    recipes_core.validate_recipe_content(_CONTENT, 'cluster')
    with pytest.raises(ValueError, match='Invalid recipe type'):
        recipes_core.validate_recipe_content(_CONTENT, 'not-a-type')
