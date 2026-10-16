"""Unit tests for resolving which auto_mounts volumes a launch will mount.

The resolution is shared by the injection path, which mounts the volumes, and
by the Kubernetes provision timeout, which has to know whether any of them is a
ReadWriteMany PVC. Anything the two could disagree about belongs here.
"""
from unittest import mock

import pytest

from sky import models
from sky.utils import status_lib
from sky.utils import volume as volume_lib


def _record(volume_type: str = 'k8s-pvc',
            access_mode: str = 'ReadWriteMany',
            user_hash: str = 'owner',
            workspace: str = 'default'):
    config = {'namespace': 'my-namespace'}
    if volume_type == 'k8s-pvc':
        config['access_mode'] = access_mode
    else:
        config['host_path'] = '/mnt/data'
    return {
        'name': 'vol',
        'handle': models.VolumeConfig(
            _version=1,
            name='vol',
            type=volume_type,
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='vol-pvc',
            size='10',
            config=config,
        ),
        'status': status_lib.VolumeStatus.READY,
        'error_message': None,
        'user_hash': user_hash,
        'workspace': 'default' if workspace is None else workspace,
    }


@pytest.fixture(name='resolve')
def resolve_fixture(monkeypatch):
    """Resolves against an in-memory config and volume DB."""

    def _resolve(auto_mounts_config, records, *, user='owner', workspace=None):
        monkeypatch.setattr(volume_lib.skypilot_config,
                            'get_effective_region_config',
                            lambda **kwargs: auto_mounts_config)
        monkeypatch.setattr(volume_lib.skypilot_config, 'get_active_workspace',
                            lambda: workspace)
        monkeypatch.setattr(volume_lib.global_user_state, 'get_volume_by_name',
                            records.get)
        monkeypatch.setattr(volume_lib.common_utils, 'get_current_user',
                            lambda: mock.MagicMock(id=user))
        return volume_lib.resolve_auto_mounts('my-context')

    return _resolve


class TestResolveAutoMounts:

    def test_no_config_resolves_to_nothing(self, resolve):
        resolution = resolve(None, {})

        assert not resolution.mounted
        assert not resolution.skipped

    def test_read_write_many_pvc_is_mounted(self, resolve):
        resolution = resolve([{
            'volume_name': 'vol',
            'mount_paths': ['~/data', '/mnt/vol']
        }], {'vol': _record()})

        assert [m.volume_name for m in resolution.mounted] == ['vol']
        assert resolution.mounted[0].mount_paths == ['~/data', '/mnt/vol']
        assert not resolution.skipped

    def test_host_path_is_mounted(self, resolve):
        """hostPath volumes have no access mode and must not be filtered out by
        the check that looks at one."""
        resolution = resolve([{
            'volume_name': 'vol',
            'mount_paths': ['/mnt/vol']
        }], {'vol': _record(volume_type='k8s-hostpath')})

        assert [m.volume_name for m in resolution.mounted] == ['vol']

    def test_missing_volume_is_skipped_loudly(self, resolve):
        resolution = resolve([{'volume_name': 'gone'}], {})

        assert not resolution.mounted
        assert len(resolution.skipped) == 1
        assert resolution.skipped[0].volume_name == 'gone'
        assert resolution.skipped[0].is_warning
        assert 'sky volumes apply' in resolution.skipped[0].message

    def test_single_writer_pvc_is_skipped_loudly(self, resolve):
        """Auto-mounting implies concurrent access from every pod."""
        resolution = resolve([{
            'volume_name': 'vol'
        }], {'vol': _record(access_mode='ReadWriteOnce')})

        assert not resolution.mounted
        assert resolution.skipped[0].is_warning
        assert 'ReadWriteOnce' in resolution.skipped[0].message

    def test_out_of_scope_volume_is_skipped_quietly(self, resolve):
        """Someone else's personal volume not applying here is normal
        operation, not something to warn about."""
        resolution = resolve([{
            'volume_name': 'vol',
            'scope': 'personal'
        }], {'vol': _record(user_hash='someone-else')},
                             user='me')

        assert not resolution.mounted
        assert not resolution.skipped[0].is_warning

    def test_entries_are_resolved_independently(self, resolve):
        """One unusable entry must not stop the others from being mounted."""
        resolution = resolve([
            {
                'volume_name': 'gone'
            },
            {
                'volume_name': 'vol'
            },
        ], {'vol': _record()})

        assert [m.volume_name for m in resolution.mounted] == ['vol']
        assert [s.volume_name for s in resolution.skipped] == ['gone']

    def test_readiness_is_not_a_filter(self, resolve):
        """A broken volume still resolves: refusing the launch is the injection
        path's job, and this also runs where raising is not allowed."""
        record = _record()
        record['status'] = status_lib.VolumeStatus.NOT_READY
        record['error_message'] = 'ProvisioningFailed: no capacity'

        resolution = resolve([{'volume_name': 'vol'}], {'vol': record})

        assert [m.volume_name for m in resolution.mounted] == ['vol']


class TestIsReadWriteManyPvc:

    def test_read_write_many_pvc(self):
        assert volume_lib.is_read_write_many_pvc(_record()['handle'])

    def test_single_writer_pvc(self):
        assert not volume_lib.is_read_write_many_pvc(
            _record(access_mode='ReadWriteOnce')['handle'])

    def test_host_path(self):
        assert not volume_lib.is_read_write_many_pvc(
            _record(volume_type='k8s-hostpath')['handle'])
