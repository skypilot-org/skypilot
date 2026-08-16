"""Tests for Kubernetes._calculate_provision_timeout."""
import types

from sky.clouds import kubernetes
from sky.utils import volume as volume_lib

_calc = kubernetes.Kubernetes._calculate_provision_timeout


def _pvc_mount(access_mode: str):
    return types.SimpleNamespace(volume_config=types.SimpleNamespace(
        type=volume_lib.VolumeType.PVC.value,
        config={'access_mode': access_mode},
    ))


def _hostpath_mount():
    return types.SimpleNamespace(volume_config=types.SimpleNamespace(
        type=volume_lib.VolumeType.HOSTPATH.value,
        config={},
    ))


def test_provision_timeout_no_volumes():
    """Default single-node timeout when there are no volume mounts."""
    assert _calc(1, None, False, False) == 10


def test_provision_timeout_rwo_pvc_extended():
    """ReadWriteOnce PVCs get more room than the 10s default.

    Late-binding storage classes (WaitForFirstConsumer) only bind the PVC
    once the consumer pod is scheduled, and the provisioner then creates the
    volume, so a slow-but-healthy provision must not be misread as
    unschedulable.
    """
    mounts = [_pvc_mount(volume_lib.VolumeAccessMode.READ_WRITE_ONCE.value)]
    assert _calc(1, mounts, False, False) == 30


def test_provision_timeout_rwx_pvc_largest():
    """ReadWriteMany (e.g. GKE filestore) keeps the longest timeout."""
    mounts = [_pvc_mount(volume_lib.VolumeAccessMode.READ_WRITE_MANY.value)]
    assert _calc(1, mounts, False, False) == 180


def test_provision_timeout_mixed_pvc_prefers_rwx():
    """A mix of RWO and RWX PVCs uses the longer RWX timeout."""
    mounts = [
        _pvc_mount(volume_lib.VolumeAccessMode.READ_WRITE_ONCE.value),
        _pvc_mount(volume_lib.VolumeAccessMode.READ_WRITE_MANY.value),
    ]
    assert _calc(1, mounts, False, False) == 180


def test_provision_timeout_non_pvc_volume_uses_default():
    """A non-PVC volume mount (e.g. hostPath) does not extend the timeout."""
    mounts = [_hostpath_mount()]
    assert _calc(1, mounts, False, False) == 10


def test_provision_timeout_queueing_overrides_all():
    """Queueing returns the long queue-managed timeout regardless of PVCs."""
    mounts = [_pvc_mount(volume_lib.VolumeAccessMode.READ_WRITE_ONCE.value)]
    assert _calc(1, mounts, False, True) == 24 * 60 * 60
