"""Unit tests for the auto-mount volume readiness gate."""
from unittest.mock import patch

import pytest

from sky import exceptions
from sky import models
from sky.backends import backend_utils
from sky.utils import status_lib


def _config(name: str, volume_type: str = 'k8s-pvc') -> models.VolumeConfig:
    return models.VolumeConfig(
        _version=1,
        name=name,
        type=volume_type,
        cloud='kubernetes',
        region='my-context',
        zone=None,
        name_on_cloud=f'{name}-pvc',
        size='10',
        config={'namespace': 'my-namespace'},
    )


def _mountable(name: str,
               status: status_lib.VolumeStatus,
               error_message=None,
               volume_type: str = 'k8s-pvc'):
    entry = {'volume_name': name, 'mount_paths': ['/mnt/shared']}
    record = {
        'name': name,
        'handle': _config(name, volume_type),
        'status': status,
        'error_message': error_message,
    }
    return entry, record


class TestCheckAutoMountVolumesReady:
    """Tests for _check_auto_mount_volumes_ready."""

    def test_no_volumes_is_noop(self):
        backend_utils._check_auto_mount_volumes_ready([])

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_ready_volume_passes(self, mock_get_errors):
        mock_get_errors.return_value = ({'vol': None}, set())
        backend_utils._check_auto_mount_volumes_ready(
            [_mountable('vol', status_lib.VolumeStatus.READY)])

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_in_use_volume_passes(self, mock_get_errors):
        mock_get_errors.return_value = ({'vol': None}, set())
        backend_utils._check_auto_mount_volumes_ready(
            [_mountable('vol', status_lib.VolumeStatus.IN_USE)])

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_not_ready_volume_is_rejected(self, mock_get_errors):
        mock_get_errors.return_value = ({
            'vol': 'PVC is pending. ProvisioningFailed: below the minimum'
        }, set())

        with pytest.raises(exceptions.VolumeNotReadyError) as exc:
            backend_utils._check_auto_mount_volumes_ready([
                _mountable('vol',
                           status_lib.VolumeStatus.NOT_READY,
                           error_message='PVC is pending.')
            ])

        assert 'vol' in str(exc.value)
        assert 'below the minimum' in str(exc.value)

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_recorded_ready_but_live_broken_is_rejected(self, mock_get_errors):
        """A volume created moments ago is recorded READY until the refresh
        daemon catches up, which is exactly when it gets auto-mounted."""
        mock_get_errors.return_value = ({'vol': 'PVC is pending.'}, set())

        with pytest.raises(exceptions.VolumeNotReadyError):
            backend_utils._check_auto_mount_volumes_ready(
                [_mountable('vol', status_lib.VolumeStatus.READY)])

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_recorded_not_ready_but_live_healthy_passes(self, mock_get_errors):
        """The recorded status lags by up to one refresh interval, so a volume
        that has since bound must not be blocked."""
        mock_get_errors.return_value = ({'vol': None}, set())

        backend_utils._check_auto_mount_volumes_ready([
            _mountable('vol',
                       status_lib.VolumeStatus.NOT_READY,
                       error_message='PVC is pending.')
        ])

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_live_check_failure_falls_back_to_recorded_ready(
            self, mock_get_errors):
        mock_get_errors.side_effect = Exception('kube API unreachable')

        backend_utils._check_auto_mount_volumes_ready(
            [_mountable('vol', status_lib.VolumeStatus.READY)])

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_live_check_failure_falls_back_to_recorded_not_ready(
            self, mock_get_errors):
        mock_get_errors.side_effect = Exception('kube API unreachable')

        with pytest.raises(exceptions.VolumeNotReadyError) as exc:
            backend_utils._check_auto_mount_volumes_ready([
                _mountable('vol',
                           status_lib.VolumeStatus.NOT_READY,
                           error_message='PVC is pending.')
            ])

        assert 'PVC is pending.' in str(exc.value)

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_undeterminable_volume_falls_back_to_recorded_status(
            self, mock_get_errors):
        """A volume the provider reports as unqueryable is not an error."""
        mock_get_errors.return_value = ({}, {'vol'})

        backend_utils._check_auto_mount_volumes_ready(
            [_mountable('vol', status_lib.VolumeStatus.READY)])

        with pytest.raises(exceptions.VolumeNotReadyError):
            backend_utils._check_auto_mount_volumes_ready(
                [_mountable('vol', status_lib.VolumeStatus.NOT_READY)])

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_hostpath_volume_passes(self, mock_get_errors):
        """hostPath volumes have no PVC, so the provider reports them clean."""
        mock_get_errors.return_value = ({'vol': None}, set())

        backend_utils._check_auto_mount_volumes_ready([
            _mountable('vol',
                       status_lib.VolumeStatus.READY,
                       volume_type='k8s-hostpath')
        ])

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_unchecked_volume_falls_back_to_recorded_status(
            self, mock_get_errors):
        """A cloud with no error check at all reports neither, which is not
        an answer -- so the recorded status decides."""
        mock_get_errors.return_value = ({}, set())

        backend_utils._check_auto_mount_volumes_ready(
            [_mountable('vol', status_lib.VolumeStatus.READY)])

        with pytest.raises(exceptions.VolumeNotReadyError):
            backend_utils._check_auto_mount_volumes_ready(
                [_mountable('vol', status_lib.VolumeStatus.NOT_READY)])

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_one_broken_volume_rejects_the_launch(self, mock_get_errors):
        mock_get_errors.return_value = ({
            'good': None,
            'bad': 'PVC is pending.'
        }, set())

        with pytest.raises(exceptions.VolumeNotReadyError) as exc:
            backend_utils._check_auto_mount_volumes_ready([
                _mountable('good', status_lib.VolumeStatus.READY),
                _mountable('bad', status_lib.VolumeStatus.READY),
            ])

        assert 'bad' in str(exc.value)

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_volumes_are_checked_in_one_call_per_cloud(self, mock_get_errors):
        mock_get_errors.return_value = ({'a': None, 'b': None}, set())

        backend_utils._check_auto_mount_volumes_ready([
            _mountable('a', status_lib.VolumeStatus.READY),
            _mountable('b', status_lib.VolumeStatus.READY),
        ])

        mock_get_errors.assert_called_once()
        _, configs = mock_get_errors.call_args[0]
        assert len(configs) == 2


class TestGateUnderDryrun:
    """Dryrun must not reach the cloud; the recorded status has to do."""

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_no_cloud_call_on_dryrun(self, mock_get_errors):
        backend_utils._check_auto_mount_volumes_ready(
            [_mountable('vol', status_lib.VolumeStatus.READY)], dryrun=True)

        mock_get_errors.assert_not_called()

    @patch('sky.backends.backend_utils.provision_lib.get_all_volumes_errors')
    def test_recorded_not_ready_still_rejected_on_dryrun(self, mock_get_errors):
        with pytest.raises(exceptions.VolumeNotReadyError):
            backend_utils._check_auto_mount_volumes_ready([
                _mountable('vol',
                           status_lib.VolumeStatus.NOT_READY,
                           error_message='PVC is pending.')
            ],
                                                          dryrun=True)

        mock_get_errors.assert_not_called()
