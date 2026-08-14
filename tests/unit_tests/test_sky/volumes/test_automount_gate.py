"""Unit tests for the volume readiness check."""
import pytest

from sky import exceptions
from sky import models
from sky.backends import backend_utils
from sky.utils import status_lib
from sky.utils import volume as volume_utils


def _record(status: status_lib.VolumeStatus,
            error_message=None,
            volume_type: str = 'k8s-pvc'):
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
            config={'namespace': 'my-namespace'},
        ),
        'status': status,
        'error_message': error_message,
    }


def _reject(record, description='Auto-mount volume', remove_hint='remove it'):
    backend_utils._reject_not_ready_volume(  # pylint: disable=protected-access
        'vol',
        record,
        description=description,
        remove_hint=remove_hint)


class TestRejectNotReadyVolume:
    """The check reads the recorded status, exactly as VolumeMount.resolve
    does when a task is submitted, so the two ways of attaching a volume
    agree."""

    def test_not_ready_volume_is_rejected(self):
        with pytest.raises(exceptions.VolumeNotReadyError) as exc:
            _reject(
                _record(status_lib.VolumeStatus.NOT_READY,
                        error_message='PVC is pending. ProvisioningFailed: '
                        'tier is invalid'))

        assert 'vol' in str(exc.value)
        assert 'tier is invalid' in str(exc.value)

    def test_rejection_without_a_recorded_reason_still_explains_itself(self):
        with pytest.raises(exceptions.VolumeNotReadyError) as exc:
            _reject(_record(status_lib.VolumeStatus.NOT_READY))

        assert 'not ready' in str(exc.value)
        assert 'refresh' in str(exc.value)

    def test_the_way_out_matches_how_the_volume_was_attached(self):
        """The one thing the two callers must not share: telling someone to
        edit their auto_mounts config over a volume they named on the task."""
        not_ready = _record(status_lib.VolumeStatus.NOT_READY)
        with pytest.raises(exceptions.VolumeNotReadyError) as auto:
            _reject(not_ready,
                    description='Auto-mount volume',
                    remove_hint='remove \'vol\' from the auto_mounts config')
        with pytest.raises(exceptions.VolumeNotReadyError) as task:
            _reject(not_ready,
                    description='Volume',
                    remove_hint='remove \'vol\' from the task\'s volumes')

        assert 'auto_mounts' in str(auto.value)
        assert 'auto_mounts' not in str(task.value)
        assert 'task' in str(task.value)

    def test_a_volume_still_being_provisioned_passes(self):
        """Not-ready but on its way: a class that binds Immediately provisions
        asynchronously, and a network filesystem takes minutes. Refusing here
        would fail a managed job's relaunch over a volume about to work, and a
        job that fails prechecks does not retry."""
        _reject(
            _record(status_lib.VolumeStatus.NOT_READY,
                    error_message=f'{volume_utils.PVC_PROVISIONING_MESSAGE} If '
                    f'this does not resolve, the storage class may be '
                    f'misconfigured. To debug, run: kubectl describe pvc vol'))

    def test_a_permanent_failure_without_a_grpc_code_is_still_rejected(self):
        """The reason to read the message for what it is rather than for a
        terminal gRPC code: an access mode no available PersistentVolume
        supports never resolves, and says so without a code."""
        with pytest.raises(exceptions.VolumeNotReadyError) as exc:
            _reject(
                _record(status_lib.VolumeStatus.NOT_READY,
                        error_message='PVC access mode mismatch: PVC requests '
                        'ReadWriteMany, but available PersistentVolumes '
                        'support: ReadWriteOnce'))

        assert 'access mode mismatch' in str(exc.value)

    def test_ready_volume_passes(self):
        _reject(_record(status_lib.VolumeStatus.READY))

    def test_in_use_volume_passes(self):
        _reject(_record(status_lib.VolumeStatus.IN_USE))

    def test_hostpath_volume_passes(self):
        _reject(
            _record(status_lib.VolumeStatus.READY, volume_type='k8s-hostpath'))

    def test_missing_status_passes(self):
        """A record without a status must not be read as broken."""
        record = _record(status_lib.VolumeStatus.READY)
        del record['status']

        _reject(record)
