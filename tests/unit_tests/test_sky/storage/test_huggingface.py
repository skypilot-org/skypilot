"""Unit tests for the Hugging Face Buckets storage backend."""
# pylint: disable=protected-access
from unittest import mock

import pytest

from sky import cloud_stores
from sky import exceptions
from sky import task as task_lib
from sky.adaptors import huggingface
from sky.data import data_utils
from sky.data import mounting_utils
from sky.data import storage


class TestSplitHfPath:
    """Tests for data_utils.split_hf_path."""

    def test_full_handle(self):
        bucket_id, sub_path = data_utils.split_hf_path(
            'hf://buckets/ns/bucket/path/to/file.txt')
        assert bucket_id == 'ns/bucket'
        assert sub_path == 'path/to/file.txt'

    def test_handle_no_sub_path(self):
        bucket_id, sub_path = data_utils.split_hf_path('hf://buckets/ns/bucket')
        assert bucket_id == 'ns/bucket'
        assert sub_path == ''

    def test_handle_trailing_slash(self):
        bucket_id, sub_path = data_utils.split_hf_path(
            'hf://buckets/ns/bucket/')
        assert bucket_id == 'ns/bucket'
        assert sub_path == ''

    def test_short_form(self):
        bucket_id, sub_path = data_utils.split_hf_path('ns/bucket/x/y.txt')
        assert bucket_id == 'ns/bucket'
        assert sub_path == 'x/y.txt'

    def test_missing_bucket_raises(self):
        with pytest.raises(ValueError, match='hf://buckets/'):
            data_utils.split_hf_path('hf://buckets/ns')

    def test_empty_segment_raises(self):
        with pytest.raises(ValueError):
            data_utils.split_hf_path('hf://buckets//bucket')


class TestIsHfPath:
    """Tests for data_utils.is_hf_path and is_hf_bucket_path."""

    @pytest.mark.parametrize('url', [
        'hf://buckets/ns/bucket',
        'hf://ns/model',
        'hf://datasets/ns/ds',
        'hf://spaces/ns/app',
    ])
    def test_any_hf_url(self, url):
        assert data_utils.is_hf_path(url) is True

    def test_s3_url(self):
        assert data_utils.is_hf_path('s3://some-bucket') is False

    def test_plain_string(self):
        assert data_utils.is_hf_path('not-a-url') is False

    def test_bucket_path(self):
        assert data_utils.is_hf_bucket_path('hf://buckets/ns/bucket') is True
        assert data_utils.is_hf_bucket_path('hf://ns/model') is False


class TestSplitHfRepoPath:
    """Tests for data_utils.split_hf_repo_path."""

    def test_model_simple(self):
        rt, rid, rev, sub = data_utils.split_hf_repo_path(
            'hf://openai/gpt-oss-20b')
        assert rt == 'model'
        assert rid == 'openai/gpt-oss-20b'
        assert rev is None
        assert sub == ''

    def test_model_with_revision(self):
        rt, rid, rev, sub = data_utils.split_hf_repo_path(
            'hf://openai-community/gpt2@v1.0')
        assert rt == 'model'
        assert rid == 'openai-community/gpt2'
        assert rev == 'v1.0'
        assert sub == ''

    def test_model_with_sub_path(self):
        rt, rid, rev, sub = data_utils.split_hf_repo_path(
            'hf://openai-community/gpt2/onnx')
        assert rt == 'model'
        assert rid == 'openai-community/gpt2'
        assert rev is None
        assert sub == 'onnx'

    def test_dataset(self):
        rt, rid, rev, _ = data_utils.split_hf_repo_path(
            'hf://datasets/open-index/hacker-news')
        assert rt == 'dataset'
        assert rid == 'open-index/hacker-news'
        assert rev is None

    def test_space_with_revision_and_sub_path(self):
        rt, rid, rev, sub = data_utils.split_hf_repo_path(
            'hf://spaces/me/demo@main/assets/img.png')
        assert rt == 'space'
        assert rid == 'me/demo'
        assert rev == 'main'
        assert sub == 'assets/img.png'

    def test_rejects_buckets_url(self):
        with pytest.raises(ValueError, match='bucket URL'):
            data_utils.split_hf_repo_path('hf://buckets/ns/bucket')

    def test_rejects_non_hf(self):
        with pytest.raises(ValueError):
            data_utils.split_hf_repo_path('s3://foo/bar')


class TestStoreTypeHf:
    """Tests for StoreType.HF enum behavior."""

    def test_enum_value(self):
        assert storage.StoreType.HF.value == 'HF'

    def test_store_prefix(self):
        assert storage.StoreType.HF.store_prefix() == 'hf://buckets/'

    def test_from_cloud(self):
        assert (storage.StoreType.from_cloud(
            huggingface.NAME) == storage.StoreType.HF)
        assert (storage.StoreType.from_cloud(
            huggingface.NAME.lower()) == storage.StoreType.HF)

    def test_to_cloud(self):
        assert storage.StoreType.HF.to_cloud() == huggingface.NAME

    def test_roundtrip_via_url(self):
        """StoreType.get_fields_from_store_url should accept HF handles."""
        result = storage.StoreType.get_fields_from_store_url(
            'hf://buckets/my-user/my-bucket/sub/path')
        store_type, bucket_name, sub_path, acct, region = result
        assert store_type == storage.StoreType.HF
        assert bucket_name == 'my-user/my-bucket'
        assert sub_path == 'sub/path'
        assert acct is None
        assert region is None


class TestValidateName:
    """Tests for HuggingFaceStore.validate_name."""

    @pytest.mark.parametrize('name', [
        'user/bucket',
        'my-org/some_bucket',
        'a/b',
        'Alice/Bob2',
        'org123/bucket.v2',
        'user/bucket-with-dashes',
    ])
    def test_valid_bucket_names(self, name):
        assert storage.HuggingFaceStore.validate_name(name) == name

    def test_accepts_full_handle(self):
        result = storage.HuggingFaceStore.validate_name(
            'hf://buckets/user/bucket')
        assert result == 'user/bucket'

    @pytest.mark.parametrize('name', [
        'just-one-part',
        '/no-namespace',
        'ns/',
        '.leading-dot/ok',
        'ok/.leading-dot',
        '-leading-dash/ok',
        'ns/has space',
        'ns//bucket',
        'ns/bucket/extra',
        '',
    ])
    def test_invalid_names_raise(self, name):
        with pytest.raises(exceptions.StorageNameError):
            storage.HuggingFaceStore.validate_name(name)

    @pytest.mark.parametrize('name, repo_type', [
        ('openai/gpt2', 'model'),
        ('datasets/open-index/hacker-news', 'dataset'),
        ('spaces/me/demo', 'space'),
    ])
    def test_valid_repo_names(self, name, repo_type):
        assert (storage.HuggingFaceStore.validate_name(
            name, repo_type=repo_type) == name)


class TestHfClassifyAndValidate:
    """Tests for classify/validate flow (bucket vs repo dispatch)."""

    def _make(self, name, source):
        # Build *without* running initialize() by short-circuiting validate
        # after classify; we just want to verify classification.
        obj = storage.HuggingFaceStore.__new__(storage.HuggingFaceStore)
        obj.name = name
        obj.source = source
        obj._repo_type = None
        obj._revision = None
        obj._hf_id = ''
        obj._classify()
        return obj

    def test_classify_bucket_from_source(self):
        o = self._make('ns/bucket', 'hf://buckets/ns/bucket')
        assert not o.is_repo
        assert o._hf_id == 'ns/bucket'

    def test_classify_model(self):
        o = self._make('', 'hf://openai/gpt2')
        assert o.is_repo
        assert o._repo_type == 'model'
        assert o._hf_id == 'openai/gpt2'
        assert o.name == 'openai/gpt2'

    def test_classify_dataset_with_revision(self):
        o = self._make('', 'hf://datasets/ns/ds@main/sub')
        assert o._repo_type == 'dataset'
        assert o._hf_id == 'datasets/ns/ds'
        assert o._revision == 'main'

    def test_classify_rejects_name_source_mismatch(self):
        with pytest.raises(exceptions.StorageSpecError,
                           match='does not match storage name'):
            self._make('wrong/name', 'hf://openai/gpt2')


class TestUploadGuards:
    """Tests that upload() fails loud for repo + local-source misconfigs."""

    def _make_repo_store(self):
        obj = storage.HuggingFaceStore.__new__(storage.HuggingFaceStore)
        obj.name = 'openai/gpt2'
        obj.source = 'hf://openai/gpt2'
        obj._repo_type = 'model'
        obj._revision = None
        obj._hf_id = 'openai/gpt2'
        obj._bucket_sub_path = None
        return obj

    def test_noop_when_source_is_repo_url(self):
        o = self._make_repo_store()
        o.upload()  # no-op, no exception

    def test_raises_on_local_source(self):
        o = self._make_repo_store()
        o.source = '/tmp/local'
        with pytest.raises(exceptions.StorageUploadError,
                           match='repos are read-only'):
            o.upload()

    def test_raises_on_list_source(self):
        o = self._make_repo_store()
        o.source = ['/tmp/a', '/tmp/b']
        with pytest.raises(exceptions.StorageUploadError,
                           match='repos are read-only'):
            o.upload()


class TestVerifyHfBucket:
    """Tests for data_utils.verify_hf_bucket."""

    @mock.patch('sky.data.data_utils.huggingface.api')
    @mock.patch('sky.data.data_utils.huggingface.get_token', return_value='t')
    def test_existing_bucket(self, mock_token, mock_api):
        del mock_token  # unused; patch fixture
        mock_api.return_value.bucket_info.return_value = mock.MagicMock()
        assert data_utils.verify_hf_bucket('ns/bucket') is True

    @mock.patch('sky.data.data_utils.huggingface.hf_hub_errors')
    @mock.patch('sky.data.data_utils.huggingface.api')
    @mock.patch('sky.data.data_utils.huggingface.get_token', return_value='t')
    def test_missing_bucket(self, mock_token, mock_api, mock_errors):
        del mock_token  # unused; patch fixture

        class _NotFound(Exception):
            pass

        errs = mock.MagicMock()
        errs.RepositoryNotFoundError = _NotFound
        errs.EntryNotFoundError = _NotFound
        mock_errors.return_value = errs
        mock_api.return_value.bucket_info.side_effect = _NotFound('missing')
        assert data_utils.verify_hf_bucket('ns/bucket') is False

    @staticmethod
    def _make_errors_mock():
        """Returns a mock ``hf_hub_errors`` module with real exception types.

        ``verify_hf_bucket`` calls ``isinstance(e, explicit_not_found_types)``,
        so the attributes have to be real types (not ``MagicMock``).
        """

        class _NotFound(Exception):
            pass

        errs = mock.MagicMock()
        errs.RepositoryNotFoundError = _NotFound
        errs.EntryNotFoundError = _NotFound
        return errs

    @mock.patch('sky.data.data_utils.huggingface.hf_hub_errors')
    @mock.patch('sky.data.data_utils.huggingface.api')
    @mock.patch('sky.data.data_utils.huggingface.get_token', return_value='t')
    def test_http_404_treated_as_missing(self, mock_token, mock_api,
                                         mock_errors):
        del mock_token  # unused; patch fixture
        mock_errors.return_value = self._make_errors_mock()
        # HTTP-style exception exposing ``response.status_code``.
        http_err = Exception('Not Found')
        http_err.response = mock.MagicMock(status_code=404)
        mock_api.return_value.bucket_info.side_effect = http_err
        assert data_utils.verify_hf_bucket('ns/bucket') is False

    @mock.patch('sky.data.data_utils.huggingface.hf_hub_errors')
    @mock.patch('sky.data.data_utils.huggingface.api')
    @mock.patch('sky.data.data_utils.huggingface.get_token', return_value='t')
    def test_http_403_propagates(self, mock_token, mock_api, mock_errors):
        del mock_token  # unused; patch fixture
        mock_errors.return_value = self._make_errors_mock()
        # Auth error: must NOT be swallowed as "not found", otherwise a caller
        # would silently try to re-create an existing private bucket.
        http_err = Exception('Forbidden')
        http_err.response = mock.MagicMock(status_code=403)
        mock_api.return_value.bucket_info.side_effect = http_err
        with pytest.raises(Exception, match='Forbidden'):
            data_utils.verify_hf_bucket('ns/bucket')

    @mock.patch('sky.data.data_utils.huggingface.hf_hub_errors')
    @mock.patch('sky.data.data_utils.huggingface.api')
    @mock.patch('sky.data.data_utils.huggingface.get_token', return_value='t')
    def test_network_error_propagates(self, mock_token, mock_api, mock_errors):
        del mock_token  # unused; patch fixture
        mock_errors.return_value = self._make_errors_mock()
        mock_api.return_value.bucket_info.side_effect = ConnectionError('boom')
        with pytest.raises(ConnectionError):
            data_utils.verify_hf_bucket('ns/bucket')


class TestHfMountCommands:
    """Tests for mounting_utils.get_hf_mount_* helpers."""

    def test_install_cmd_mentions_release(self):
        cmd = mounting_utils.get_hf_mount_install_cmd()
        assert mounting_utils.HF_MOUNT_VERSION in cmd
        # We use the FUSE backend binary; NFS isn't fetched.
        assert 'hf-mount-fuse' in cmd
        assert 'hf-mount-nfs' not in cmd
        # Must bootstrap fuse3 on hosts that don't already have it (minimal
        # container images such as the stock ubuntu image don't).
        assert 'fuse3' in cmd

    def test_mount_cmd_default(self):
        cmd = mounting_utils.get_hf_mount_cmd('user/bucket', '/mnt/data')
        assert 'hf-mount start' in cmd
        assert 'bucket user/bucket /mnt/data' in cmd
        assert '--token-file' in cmd
        # We always request the FUSE backend; hf-mount defaults to NFS
        # otherwise, which needs kernel NFS-client support.
        assert '--fuse' in cmd
        assert '--read-only' not in cmd
        # ``hf-mount start`` uses clap trailing-var-args to forward unknown
        # options to the backend binary, and stops option parsing at the
        # first unknown arg. If ``--fuse`` comes AFTER ``--token-file``
        # (injected dynamically from ``$TOKEN_FILE_ARG``), the daemon
        # silently falls back to NFS and fails with "failed to exec
        # hf-mount-nfs". Pin the order so this regression can't sneak back.
        fuse_idx = cmd.find('--fuse')
        token_arg_idx = cmd.find('$TOKEN_FILE_ARG')
        assert fuse_idx != -1 and token_arg_idx != -1
        assert fuse_idx < token_arg_idx, (
            f'--fuse must appear before $TOKEN_FILE_ARG in the generated '
            f'mount command, otherwise the NFS backend will be chosen. '
            f'Got: {cmd!r}')

    def test_mount_cmd_read_only(self):
        cmd = mounting_utils.get_hf_mount_cmd('user/bucket',
                                              '/mnt/data',
                                              read_only=True)
        assert '--read-only' in cmd
        assert '--fuse' in cmd

    def test_mount_cmd_sub_path(self):
        cmd = mounting_utils.get_hf_mount_cmd('user/bucket',
                                              '/mnt/data',
                                              _bucket_sub_path='logs')
        # shlex.quote only adds quotes when the argument contains special
        # characters; plain ``user/bucket/logs`` is passed verbatim.
        assert 'user/bucket/logs' in cmd

    def test_mount_cmd_repo_mode(self):
        cmd = mounting_utils.get_hf_mount_cmd('openai/gpt2',
                                              '/mnt/model',
                                              mode='repo',
                                              revision='v1.0')
        assert 'repo openai/gpt2 /mnt/model' in cmd
        assert '--revision v1.0' in cmd
        assert '--fuse' in cmd
        # Repos are always read-only for hf-mount; no flag needed.
        assert '--read-only' not in cmd

    def test_mount_cmd_dataset_mode(self):
        cmd = mounting_utils.get_hf_mount_cmd('datasets/ns/ds',
                                              '/mnt/data',
                                              mode='repo')
        assert 'repo datasets/ns/ds /mnt/data' in cmd

    def test_mount_cmd_invalid_mode(self):
        with pytest.raises(ValueError, match='hf-mount mode'):
            mounting_utils.get_hf_mount_cmd('x/y', '/mnt', mode='invalid')


class TestStorageIntegration:
    """Tests for public Storage/Task integration paths."""

    @mock.patch(
        'sky.data.storage.global_user_state.get_handle_from_storage_name',
        return_value=None)
    @mock.patch('sky.data.storage.huggingface.huggingface_hub.load_module')
    @mock.patch('sky.data.storage.huggingface.get_token', return_value=None)
    @mock.patch('sky.data.storage.huggingface.api')
    def test_public_repo_construct_without_credentials(self, mock_api,
                                                       mock_token, mock_load,
                                                       mock_handle):
        del mock_token, mock_load, mock_handle
        mock_api.return_value.repo_info.return_value = mock.MagicMock()
        storage_obj = storage.Storage.from_yaml_config({
            'source': 'hf://openai-community/gpt2',
            'store': 'hf',
            'mode': 'MOUNT',
        })

        storage_obj.construct()

        assert storage_obj.name == 'openai-community/gpt2'
        assert storage.StoreType.HF in storage_obj.stores
        mock_api.return_value.repo_info.assert_called_once_with(
            repo_id='openai-community/gpt2',
            repo_type='model',
            revision=None,
            token=None)

    @mock.patch(
        'sky.data.storage.global_user_state.get_handle_from_storage_name',
        return_value=None)
    @mock.patch('sky.data.storage._is_storage_cloud_enabled', return_value=True)
    @mock.patch('sky.data.storage.huggingface.get_token', return_value='token')
    @mock.patch('sky.data.storage.huggingface.api')
    def test_bucket_source_construct_infers_name_and_sub_path(
            self, mock_api, mock_token, mock_enabled, mock_handle):
        del mock_token, mock_enabled, mock_handle
        mock_api.return_value.bucket_info.return_value = mock.MagicMock()
        storage_obj = storage.Storage.from_yaml_config({
            'source': 'hf://buckets/ns/bucket/path/to/data',
            'store': 'hf',
            'mode': 'MOUNT',
        })

        storage_obj.construct()

        assert storage_obj.name == 'ns/bucket'
        assert storage_obj._bucket_sub_path == 'path/to/data'
        hf_store = storage_obj.stores[storage.StoreType.HF]
        assert hf_store is not None
        assert hf_store._bucket_sub_path == 'path/to/data'

    def test_copy_mode_translates_hf_bucket_to_file_mount(self):
        storage_obj = storage.Storage(name='ns/bucket',
                                      source=['/tmp/a'],
                                      stores=[storage.StoreType.HF],
                                      mode=storage.StorageMode.COPY)
        storage_obj.stores = {storage.StoreType.HF: mock.MagicMock()}
        storage_obj.construct = mock.Mock()
        task = task_lib.Task()
        task.storage_mounts = {'/mnt/data': storage_obj}
        task.storage_plans = {}

        task.sync_storage_mounts()

        assert task.file_mounts['/mnt/data'] == 'hf://buckets/ns/bucket'

    def test_copy_mode_keeps_hf_repo_source(self):
        storage_obj = storage.Storage(name='openai-community/gpt2',
                                      source='hf://openai-community/gpt2',
                                      stores=[storage.StoreType.HF],
                                      mode=storage.StorageMode.COPY)
        storage_obj.stores = {storage.StoreType.HF: mock.MagicMock()}
        storage_obj.construct = mock.Mock()
        task = task_lib.Task()
        task.storage_mounts = {'/mnt/model': storage_obj}
        task.storage_plans = {}

        task.sync_storage_mounts()

        assert task.file_mounts['/mnt/model'] == 'hf://openai-community/gpt2'


class TestHfCredentials:
    """Tests for HF token file normalization."""

    def test_env_token_is_written_to_canonical_remote_mount(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('HF_TOKEN', 'token-from-env')

        mounts = huggingface.get_credential_file_mounts()

        assert mounts == {
            huggingface.HF_TOKEN_PATH: huggingface.HF_TOKEN_PATH_ENV_CACHE
        }
        token_path = tmp_path / '.sky' / 'huggingface' / 'token'
        assert token_path.read_text(encoding='utf-8') == 'token-from-env'

    def test_legacy_token_mounts_to_canonical_remote_path(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        legacy_dir = tmp_path / '.huggingface'
        legacy_dir.mkdir()
        (legacy_dir / 'token').write_text('legacy-token', encoding='utf-8')

        mounts = huggingface.get_credential_file_mounts()

        assert mounts == {
            huggingface.HF_TOKEN_PATH: huggingface.HF_TOKEN_PATH_LEGACY
        }


class TestHfCloudStorage:
    """Tests for HF COPY-mode cloud store commands."""

    def test_registry_includes_hf(self):
        assert isinstance(cloud_stores.get_storage_from_path('hf://x/y'),
                          cloud_stores.HFCloudStorage)

    def test_bucket_file_download_command(self):
        command = cloud_stores.HFCloudStorage().make_sync_file_command(
            'hf://buckets/ns/bucket/file.txt', '/tmp/file.txt')
        assert 'huggingface_hub' in command
        assert 'download_bucket_files' in command

    def test_repo_dir_download_command(self):
        command = cloud_stores.HFCloudStorage().make_sync_dir_command(
            'hf://datasets/ns/ds@main/subdir', '/tmp/ds')
        assert 'snapshot_download' in command
        assert "'dataset'" in command
        assert 'subdir/*' in command

    @mock.patch('sky.cloud_stores.huggingface.get_token', return_value=None)
    @mock.patch('sky.cloud_stores.huggingface.api')
    def test_repo_is_directory_uses_list_repo_tree(self, mock_api, mock_token):
        """Directory check must go through list_repo_tree, not repo_info.

        repo_info eagerly fetches metadata for every file in the repo
        (siblings), which is slow on large repos. list_repo_tree only
        fetches the specified path.
        """
        del mock_token
        mock_api.return_value.list_repo_tree.return_value = iter([])
        result = cloud_stores.HFCloudStorage().is_directory(
            'hf://openai-community/gpt2/onnx')
        assert result is True
        mock_api.return_value.list_repo_tree.assert_called_once()
        mock_api.return_value.repo_info.assert_not_called()

    @mock.patch('sky.cloud_stores.huggingface.hf_hub_errors')
    @mock.patch('sky.cloud_stores.huggingface.get_token', return_value=None)
    @mock.patch('sky.cloud_stores.huggingface.api')
    def test_repo_is_directory_file_path_returns_false(self, mock_api,
                                                       mock_token, mock_errors):
        """File paths should raise EntryNotFoundError -> returns False."""
        del mock_token

        class _EntryNotFound(Exception):
            pass

        errs = mock.MagicMock()
        errs.EntryNotFoundError = _EntryNotFound
        errs.RepositoryNotFoundError = _EntryNotFound
        mock_errors.return_value = errs
        mock_api.return_value.list_repo_tree.side_effect = _EntryNotFound(
            'not found')
        assert cloud_stores.HFCloudStorage().is_directory(
            'hf://openai-community/gpt2/config.json') is False


class TestHfMountCachedCommand:
    """Tests for HuggingFaceStore.mount_cached_command propagation."""

    def _make_bucket_store(self):
        obj = storage.HuggingFaceStore.__new__(storage.HuggingFaceStore)
        obj.name = 'ns/bucket'
        obj.source = None
        obj._repo_type = None
        obj._revision = None
        obj._hf_id = 'ns/bucket'
        obj._bucket_sub_path = None
        return obj

    def test_mount_cached_no_config(self):
        o = self._make_bucket_store()
        cmd = o.mount_cached_command('/mnt/data', config=None)
        # No --read-only flag when config is unset.
        assert '--read-only' not in cmd

    def test_mount_cached_with_read_only(self):
        o = self._make_bucket_store()
        cfg = storage.MountCachedConfig(read_only=True)
        cmd = o.mount_cached_command('/mnt/data', config=cfg)
        assert '--read-only' in cmd

    def test_mount_cached_ignores_rclone_only_fields(self):
        """rclone-specific fields should not produce any hf-mount args."""
        o = self._make_bucket_store()
        cfg = storage.MountCachedConfig(
            transfers=16,
            buffer_size='128M',
            vfs_cache_max_size='10G',
            vfs_read_chunk_streams=8,
        )
        cmd = o.mount_cached_command('/mnt/data', config=cfg)
        # None of these rclone flags should appear in the hf-mount command.
        for flag in ('--transfers', '--buffer-size', '--vfs-cache-max-size',
                     '--vfs-read-chunk-streams'):
            assert flag not in cmd
