"""Unit tests for database-backed managed job file storage."""

import contextlib
import os
from typing import Dict, Optional

import filelock
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky import skypilot_config
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
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)

    state.create_table(engine)

    yield engine


def _create_basic_job(tmp_path,
                      *,
                      name: str = 'test-job',
                      store_content: bool = True,
                      set_paths: bool = False,
                      config_content: Optional[str] = None) -> Dict[str, str]:
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
        state.scheduler_set_waiting([job_id],
                                    dag_content,
                                    user_yaml_content,
                                    env_content,
                                    config_file_content=config_content,
                                    priority=100)

    if set_paths:
        with state._db_manager.get_engine().begin() as conn:  # pylint: disable=protected-access
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

    with state._db_manager.get_engine().begin() as conn:  # pylint: disable=protected-access
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

    with state._db_manager.get_engine().begin() as conn:  # pylint: disable=protected-access
        conn.execute(
            sqlalchemy.update(state.job_info_table).where(  # pylint: disable=protected-access
                state.job_info_table.c.spot_job_id == job_id).values(
                    dag_yaml_content=None,))

    os.remove(job_info['dag_path'])

    assert file_content_utils.get_job_dag_content(job_id) is None


_CONFIG_CONTENT = ('kubernetes:\n'
                   '  allowed_contexts:\n'
                   '  - ctx-a\n'
                   '  - ctx-b\n')


def test_restore_job_config_file_writes_content(_mock_managed_jobs_db_conn,
                                                tmp_path, monkeypatch):
    config_path = tmp_path / 'job.config_yaml'
    monkeypatch.setenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
                       str(config_path))
    job_info = _create_basic_job(tmp_path,
                                 name='config-job',
                                 config_content=_CONFIG_CONTENT)

    file_content_utils.restore_job_config_file(job_info['job_id'])

    assert config_path.read_text(encoding='utf-8') == _CONFIG_CONTENT
    # The config can carry credentials, so keep it owner-only.
    assert (config_path.stat().st_mode & 0o777) == 0o600


def test_restore_job_config_file_replaces_atomically(_mock_managed_jobs_db_conn,
                                                     tmp_path, monkeypatch):
    """A concurrent reader must never observe a truncated config.

    Jobs submitted as one ``--num-jobs`` batch share a single config path
    and every job's controller restores it. An O_TRUNC rewrite empties the
    path in place, so another job reading it mid-write loads ``{}`` and
    silently runs with no config -- which collapses
    ``allowed_contexts`` and fails the job's prechecks. Restoring must
    swap in a fully-written file instead.
    """
    config_path = tmp_path / 'shared.config_yaml'
    stale_content = 'kubernetes:\n  allowed_contexts:\n  - ctx-stale\n'
    config_path.write_text(stale_content, encoding='utf-8')
    original_inode = config_path.stat().st_ino

    monkeypatch.setenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
                       str(config_path))
    job_info = _create_basic_job(tmp_path,
                                 name='shared-config-job',
                                 config_content=_CONFIG_CONTENT)

    # Stand in for another job's controller that opened the shared path
    # just before this restore starts.
    with open(config_path, 'r', encoding='utf-8') as concurrent_reader:
        file_content_utils.restore_job_config_file(job_info['job_id'])

        # The reader holds the pre-restore inode and still sees a whole
        # file. Under an in-place rewrite it would read '' or a fragment.
        assert concurrent_reader.read() == stale_content

    assert config_path.read_text(encoding='utf-8') == _CONFIG_CONTENT
    assert config_path.stat().st_ino != original_inode
    # No tmp fragments left next to the destination.
    assert not [p for p in tmp_path.iterdir() if p.name.endswith('.tmp')]
