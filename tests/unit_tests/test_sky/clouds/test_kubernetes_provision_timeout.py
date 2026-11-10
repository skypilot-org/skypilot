"""Unit tests for the Kubernetes provision timeout.

A ReadWriteMany PVC is backed by a network filesystem that can take minutes to
provision, so it raises the timeout. Auto-mounted volumes are the same kind of
volume, reached by a different code path, and must get the same allowance.
"""
from sky import clouds
from sky import models
from sky.utils import volume as volume_lib

_BASE_TIMEOUT = 10
# Long enough for the network filesystem behind the volume to be created: a
# 1 TiB GKE Filestore instance measured ~7 minutes.
_RWX_TIMEOUT = 600


def _volume_config(volume_type: str = 'k8s-pvc',
                   access_mode: str = 'ReadWriteMany'):
    config = {'namespace': 'my-namespace'}
    if volume_type == 'k8s-pvc':
        config['access_mode'] = access_mode
    return models.VolumeConfig(
        _version=1,
        name='vol',
        type=volume_type,
        cloud='kubernetes',
        region='my-context',
        zone=None,
        name_on_cloud='vol-pvc',
        size='10',
        config=config,
    )


def _task_volume(**kwargs):
    return volume_lib.VolumeMount(path='/mnt/vol',
                                  volume_name='vol',
                                  volume_config=_volume_config(**kwargs))


def _ephemeral_volume(**kwargs):
    """A volume declared inline on the task, as the task parser leaves it."""
    return volume_lib.VolumeMount.resolve_ephemeral_config(
        '/mnt/vol', {
            'size': '10',
            **kwargs
        })


def _auto_mount(**kwargs):
    return volume_lib.AutoMount(volume_name='vol',
                                record={'handle': _volume_config(**kwargs)},
                                mount_paths=['/mnt/vol'])


def _timeout(*, volume_mounts=None, auto_mounts=None, **kwargs):
    kwargs.setdefault('num_nodes', 1)
    kwargs.setdefault('enable_flex_start', False)
    kwargs.setdefault('is_using_queueing', False)
    return clouds.Kubernetes._calculate_provision_timeout(
        volume_mounts=volume_mounts, auto_mounts=auto_mounts, **kwargs)


class TestProvisionTimeoutWithAutoMounts:

    def test_auto_mounted_read_write_many_pvc_extends_the_timeout(self):
        assert _timeout(auto_mounts=[_auto_mount()]) == _RWX_TIMEOUT

    def test_auto_mounted_host_path_does_not(self):
        """hostPath needs no provisioning, so there is nothing to wait for."""
        assert _timeout(auto_mounts=[_auto_mount(
            volume_type='k8s-hostpath')]) == _BASE_TIMEOUT

    def test_one_extending_volume_among_several_is_enough(self):
        assert _timeout(auto_mounts=[
            _auto_mount(volume_type='k8s-hostpath'),
            _auto_mount(),
        ]) == _RWX_TIMEOUT

    def test_a_task_volume_still_extends_the_timeout(self):
        assert _timeout(volume_mounts=[_task_volume()]) == _RWX_TIMEOUT

    def test_the_two_kinds_of_volume_agree(self):
        """The point of the change: the same volume gets the same timeout
        whether it is declared on the task or mounted from the config."""
        assert (_timeout(volume_mounts=[_task_volume()]) == _timeout(
            auto_mounts=[_auto_mount()]))

    def test_an_ephemeral_read_write_many_volume_extends_the_timeout(self):
        """Its type is only resolved when it is provisioned, which is after
        this runs; on Kubernetes an unset type can only mean a PVC."""
        assert _timeout(volume_mounts=[
            _ephemeral_volume(config={'access_mode': 'ReadWriteMany'})
        ]) == _RWX_TIMEOUT

    def test_an_ephemeral_volume_with_an_explicit_type_agrees(self):
        assert _timeout(volume_mounts=[
            _ephemeral_volume(type='k8s-pvc',
                              config={'access_mode': 'ReadWriteMany'})
        ]) == _RWX_TIMEOUT

    def test_an_ephemeral_single_writer_volume_leaves_the_timeout_alone(self):
        """The default access mode, resolved during provisioning."""
        assert _timeout(volume_mounts=[_ephemeral_volume()]) == _BASE_TIMEOUT

    def test_only_an_ephemeral_volume_may_leave_its_type_unset(self):
        """A persistent volume's type comes from the volume DB, so an unset
        one is not a PVC waiting to be resolved."""
        mount = _task_volume()
        mount.volume_config.type = ''
        assert _timeout(volume_mounts=[mount]) == _BASE_TIMEOUT

    def test_no_volumes_leaves_the_timeout_alone(self):
        assert _timeout() == _BASE_TIMEOUT

    def test_single_writer_pvc_leaves_the_timeout_alone(self):
        assert _timeout(auto_mounts=[_auto_mount(
            access_mode='ReadWriteOnce')]) == _BASE_TIMEOUT

    def test_queueing_still_wins(self):
        """Admission is waited on separately; volumes must not shorten it."""
        assert _timeout(auto_mounts=[_auto_mount()],
                        is_using_queueing=True) == 24 * 60 * 60

    def test_flex_start_still_wins(self):
        """Flex start's allowance is already longer than the volume one."""
        assert _timeout(auto_mounts=[_auto_mount()],
                        enable_flex_start=True) == 1200
