import hashlib
from unittest import mock

import pytest

from sky import exceptions
from sky import Task
from sky.data.storage import S3Store
from sky.utils import controller_utils


@pytest.mark.parametrize('username,user_hash', [
    ('a' * 24, 'bfc5e485'),
    ('a' * 25, 'bfc5e485'),
    ('a' * 26, 'bfc5e485'),
    ('a' * 63, 'bfc5e485'),
    ('a' * 63, 'b' * 36),
])
def test_generated_bucket_fits_s3(username, user_hash, tmp_path):
    task = Task(workdir=str(tmp_path))
    seen = []

    def sync():
        for storage in task.storage_mounts.values():
            seen.append(storage.name)
            S3Store.validate_name(storage.name)
        raise RuntimeError('stop before cloud upload')

    with mock.patch.object(controller_utils.common_utils,
                           'get_cleaned_username', return_value=username), \
         mock.patch.object(controller_utils.common_utils,
                           'get_user_hash', return_value=user_hash), \
         mock.patch.object(controller_utils, '_generate_run_uuid',
                           return_value='56j9fnwc'), \
         mock.patch.object(controller_utils.skypilot_config, 'get_nested',
                           return_value=None), \
         mock.patch.object(task, 'sync_storage_mounts', side_effect=sync):
        with pytest.raises(RuntimeError, match='stop before cloud upload'):
            controller_utils.maybe_translate_local_file_mounts_and_sync_up(
                task, 'serve')
    if len(user_hash) > 16:
        user_hash = hashlib.sha256(user_hash.encode()).hexdigest()[:16]
    username_limit = 63 - len(f'skypilot-filemounts--{user_hash}-56j9fnwc')
    assert seen == [
        f'skypilot-filemounts-{username[:username_limit]}-'
        f'{user_hash}-56j9fnwc'
    ]


def test_storage_error_is_not_masked(tmp_path):
    task = Task(workdir=str(tmp_path))
    with mock.patch.object(controller_utils.skypilot_config, 'get_nested',
                           return_value=None), \
         mock.patch.object(task, 'sync_storage_mounts',
                           side_effect=exceptions.StorageNameError('bad name')):
        with pytest.raises(exceptions.StorageNameError, match='bad name'):
            controller_utils.maybe_translate_local_file_mounts_and_sync_up(
                task, 'serve')
