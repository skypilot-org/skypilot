"""Tests for sky.provision.kubernetes.volume delete behavior."""
from unittest import mock

from sky import models
from sky.provision.kubernetes import volume as volume_provision


def _make_pvc_config(use_existing: bool) -> models.VolumeConfig:
    return models.VolumeConfig(
        name='test-vol',
        type='k8s-pvc',
        cloud='kubernetes',
        region='my-context',
        zone=None,
        name_on_cloud='real-pvc',
        size='5',
        config={
            'namespace': 'default',
            'use_existing': use_existing,
        },
    )


def test_delete_pvc_volume_destructive_when_not_use_existing():
    config = _make_pvc_config(use_existing=False)
    with mock.patch.object(volume_provision, 'kubernetes') as mock_k8s, \
         mock.patch.object(volume_provision,
                           'kubernetes_utils') as mock_k8s_utils:
        volume_provision._delete_pvc_volume(config)
        mock_k8s_utils.delete_k8s_resource_with_retry.assert_called_once()
        # The PVC delete lambda should have been passed in.
        _, kwargs = mock_k8s_utils.delete_k8s_resource_with_retry.call_args
        assert kwargs['resource_type'] == 'pvc'
        assert kwargs['resource_name'] == 'real-pvc'


def test_delete_pvc_volume_preserves_pvc_when_use_existing():
    config = _make_pvc_config(use_existing=True)
    with mock.patch.object(volume_provision, 'kubernetes') as mock_k8s, \
         mock.patch.object(volume_provision,
                           'kubernetes_utils') as mock_k8s_utils:
        result = volume_provision._delete_pvc_volume(config)
        # Must NOT have attempted to delete the PVC.
        mock_k8s_utils.delete_k8s_resource_with_retry.assert_not_called()
        mock_k8s.core_api.assert_not_called()
        # Still returns the config so the caller can proceed to unregister.
        assert result is config
