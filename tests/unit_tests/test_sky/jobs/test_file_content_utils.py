"""Unit tests for database-backed managed job file storage."""

import contextlib
import os
from typing import Dict

import filelock
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import file_content_utils
from sky.jobs import state


@pytest.fixture
def _mock_managed_jobs_db_conn(tmp_path, monkeypatch):
    """Set up an isolated managed jobs database for tests."""

    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    @contextlib.contextmanager
    def _tmp_db_lock(section: str):
        lock_path = tmp_path / f'.{section}.lock'
        with filelock.FileLock(str(lock_path), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)
    monkeypatch.setattr(state, '_SQLALCHEMY_ENGINE', engine)
    monkeypatch.setattr(state, '_SQLALCHEMY_ENGINE_ASYNC', async_engine)

    state.create_table(engine)

    yield engine


def _create_basic_job(tmp_path,
                      *,
                      name: str = 'test-job',
                      store_content: bool = True,
                      set_paths: bool = False) -> Dict[str, str]:
    dag_path = tmp_path / f'{name}.yaml'
    env_path = tmp_path / f'{name}.env'
    user_yaml_path = tmp_path / f'{name}.user.yaml'

    dag_content = 'name: job\ncommands:\n  - echo "hello"\n'
    env_content = 'FOO=bar\n'
    user_yaml_content = 'run: echo user\n'

    dag_path.write_text(dag_content, encoding='utf-8')
    env_path.write_text(env_content, encoding='utf-8')
    user_yaml_path.write_text(user_yaml_content, encoding='utf-8')

    job_id = state.set_job_info_without_job_id(name=name,
                                               workspace='workspace',
                                               entrypoint='entrypoint',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='user')
    state.set_pending(job_id,
                      task_id=0,
                      task_name='task0',
                      resources_str='{}',
                      metadata='{}')

    if store_content:
        state.scheduler_set_waiting(job_id,
                                    dag_content,
                                    user_yaml_content,
                                    env_content,
                                    config_file_content=None,
                                    priority=100)

    if set_paths:
        with state._SQLALCHEMY_ENGINE.begin() as conn:  # pylint: disable=protected-access
            conn.execute(
                sqlalchemy.update(state.job_info_table).where(  # pylint: disable=protected-access
                    state.job_info_table.c.spot_job_id == job_id).values(
                        dag_yaml_path=str(dag_path),
                        env_file_path=str(env_path),
                    ))

    return {
        'job_id': job_id,
        'dag_path': str(dag_path),
        'env_path': str(env_path),
        'dag_content': dag_content,
        'env_content': env_content,
    }


def test_get_job_content_from_database(_mock_managed_jobs_db_conn, tmp_path):
    job_info = _create_basic_job(tmp_path)
    job_id = job_info['job_id']

    file_info = state.get_job_file_contents(job_id)
    assert file_info['dag_yaml_path'] is None
    assert file_info['env_file_path'] is None

    assert file_content_utils.get_job_dag_content(
        job_id) == job_info['dag_content']
    assert file_content_utils.get_job_env_content(
        job_id) == job_info['env_content']

    os.remove(job_info['dag_path'])
    os.remove(job_info['env_path'])

    assert file_content_utils.get_job_dag_content(
        job_id) == job_info['dag_content']
    assert file_content_utils.get_job_env_content(
        job_id) == job_info['env_content']


def test_get_job_content_falls_back_to_disk(_mock_managed_jobs_db_conn,
                                            tmp_path):
    job_info = _create_basic_job(tmp_path,
                                 name='fallback-job',
                                 store_content=False,
                                 set_paths=True)
    job_id = job_info['job_id']

    assert file_content_utils.get_job_dag_content(
        job_id) == job_info['dag_content']
    assert file_content_utils.get_job_env_content(
        job_id) == job_info['env_content']


def test_get_job_env_content_missing_returns_none(_mock_managed_jobs_db_conn,
                                                  tmp_path):
    job_info = _create_basic_job(tmp_path, name='missing-env')
    job_id = job_info['job_id']

    with state._SQLALCHEMY_ENGINE.begin() as conn:  # pylint: disable=protected-access
        conn.execute(
            sqlalchemy.update(state.job_info_table).where(  # pylint: disable=protected-access
                state.job_info_table.c.spot_job_id == job_id).values(
                    env_file_content=None,))

    os.remove(job_info['env_path'])

    assert file_content_utils.get_job_env_content(job_id) is None


def test_get_job_dag_content_missing_returns_none(_mock_managed_jobs_db_conn,
                                                  tmp_path):
    job_info = _create_basic_job(tmp_path, name='missing-dag')
    job_id = job_info['job_id']

    with state._SQLALCHEMY_ENGINE.begin() as conn:  # pylint: disable=protected-access
        conn.execute(
            sqlalchemy.update(state.job_info_table).where(  # pylint: disable=protected-access
                state.job_info_table.c.spot_job_id == job_id).values(
                    dag_yaml_content=None,))

    os.remove(job_info['dag_path'])

    assert file_content_utils.get_job_dag_content(job_id) is None
