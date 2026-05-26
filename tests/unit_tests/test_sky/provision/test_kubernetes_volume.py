"""Tests for sky.provision.kubernetes.volume delete + apply behavior."""
from types import SimpleNamespace
from unittest import mock

import pytest

from sky import models
from sky.provision.kubernetes import config as k8s_config
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


# ── _apply_pvc_volume: empty-default StorageClass safety net ──────────────


def _make_sc(name, annotations=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, annotations=annotations or {}))


def _apply_config(storage_class_name=None) -> models.VolumeConfig:
    """Build a minimal PVC volume config for _apply_pvc_volume tests."""
    config = {
        'namespace': 'default',
        'access_mode': 'ReadWriteOnce',
    }
    if storage_class_name is not None:
        config['storage_class_name'] = storage_class_name
    return models.VolumeConfig(
        name='test-vol',
        type='k8s-pvc',
        cloud='kubernetes',
        region='my-context',
        zone=None,
        name_on_cloud='real-pvc',
        size='5',
        config=config,
    )


def _patch_apply_dependencies(mock_k8s):
    """Common patch wiring for _apply_pvc_volume happy-path mocking."""
    # api_exception() returns an exception class; produce a stable one.
    mock_k8s.api_exception.return_value = type('FakeApiException', (Exception,),
                                               {})
    mock_k8s.API_TIMEOUT = 5
    # read_storage_class succeeds by default (no raise).
    mock_k8s.storage_api.return_value.read_storage_class.return_value = None


def test_apply_pvc_explicit_storage_class_uses_read_storage_class():
    """O4: explicit storage_class_name → existing read_storage_class path."""
    config = _apply_config(storage_class_name='premium-rwx')
    with mock.patch.object(volume_provision, 'kubernetes') as mock_k8s, \
         mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        _patch_apply_dependencies(mock_k8s)
        volume_provision._apply_pvc_volume(config)
        # The pre-flight is the existing per-name check.
        mock_k8s.storage_api.return_value.read_storage_class \
            .assert_called_once()
        # The empty-default branch must not have run.
        mock_k8s.storage_api.return_value.list_storage_class \
            .assert_not_called()
        mock_create.assert_called_once()


def test_apply_pvc_omitted_storage_class_succeeds_with_default_present():
    """O1: omitted storage_class_name + cluster has a default → proceeds."""
    config = _apply_config(storage_class_name=None)
    with mock.patch.object(volume_provision, 'kubernetes') as mock_k8s, \
         mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        _patch_apply_dependencies(mock_k8s)
        mock_k8s.storage_api.return_value.list_storage_class.return_value = (
            SimpleNamespace(items=[
                _make_sc('std', annotations={}),
                _make_sc(
                    'default-sc',
                    annotations={
                        'storageclass.kubernetes.io/is-default-class': 'true'
                    }),
            ]))
        volume_provision._apply_pvc_volume(config)
        mock_k8s.storage_api.return_value.list_storage_class \
            .assert_called_once()
        # No per-name validation when nothing was specified.
        mock_k8s.storage_api.return_value.read_storage_class \
            .assert_not_called()
        mock_create.assert_called_once()


def test_apply_pvc_omitted_storage_class_legacy_beta_annotation_recognized():
    """O1 variant: legacy beta annotation also counts as default."""
    config = _apply_config(storage_class_name=None)
    with mock.patch.object(volume_provision, 'kubernetes') as mock_k8s, \
         mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        _patch_apply_dependencies(mock_k8s)
        legacy_annotation = ('storageclass.beta.kubernetes.io/is-default-class')
        mock_k8s.storage_api.return_value.list_storage_class.return_value = (
            SimpleNamespace(items=[
                _make_sc('legacy-default',
                         annotations={legacy_annotation: 'true'}),
            ]))
        volume_provision._apply_pvc_volume(config)
        mock_create.assert_called_once()


def test_apply_pvc_omitted_storage_class_capitalized_truthy_recognized():
    """O1 variant: K8s admission accepts True/TRUE/1/t/T — we must too.

    Strict equality on `'true'` would miss `'True'`-annotated defaults
    emitted by some Helm charts in the wild.
    """
    default_annotation = 'storageclass.kubernetes.io/is-default-class'
    for truthy in ('True', 'TRUE', '1', 't', 'T'):
        config = _apply_config(storage_class_name=None)
        with mock.patch.object(volume_provision, 'kubernetes') as mock_k8s, \
             mock.patch.object(volume_provision,
                               'create_persistent_volume_claim') as mock_create:
            _patch_apply_dependencies(mock_k8s)
            storage_api_mock = mock_k8s.storage_api.return_value
            storage_api_mock.list_storage_class.return_value = (SimpleNamespace(
                items=[
                    _make_sc(f'sc-{truthy}',
                             annotations={default_annotation: truthy}),
                ]))
            volume_provision._apply_pvc_volume(config)
            assert mock_create.call_count == 1, (
                f'expected {truthy!r} to be treated as a default annotation')


def test_apply_pvc_omitted_storage_class_no_default_raises():
    """O2: omitted + no default-annotated SC → KubernetesError, no create."""
    config = _apply_config(storage_class_name=None)
    with mock.patch.object(volume_provision, 'kubernetes') as mock_k8s, \
         mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        _patch_apply_dependencies(mock_k8s)
        mock_k8s.storage_api.return_value.list_storage_class.return_value = (
            SimpleNamespace(items=[
                _make_sc('std', annotations={}),
                # 'false' annotation must NOT be treated as default.
                _make_sc(
                    'not-default',
                    annotations={
                        'storageclass.kubernetes.io/is-default-class': 'false'
                    }),
            ]))
        with pytest.raises(k8s_config.KubernetesError) as exc:
            volume_provision._apply_pvc_volume(config)
        msg = str(exc.value)
        assert 'No storage class specified' in msg
        assert repr('my-context') in msg
        assert 'config.storage_class_name' in msg
        mock_create.assert_not_called()


def test_apply_pvc_omitted_multiple_defaults_proceeds():
    """O3: multiple SCs annotated default → proceeds (K8s picks one).

    We do not reject this server-side; the dashboard warns when the path
    goes through the UI, but CLI/SDK callers behave consistently with K8s
    itself (which picks one nondeterministically and binds the PVC).
    """
    config = _apply_config(storage_class_name=None)
    with mock.patch.object(volume_provision, 'kubernetes') as mock_k8s, \
         mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        _patch_apply_dependencies(mock_k8s)
        mock_k8s.storage_api.return_value.list_storage_class.return_value = (
            SimpleNamespace(items=[
                _make_sc(
                    'default-a',
                    annotations={
                        'storageclass.kubernetes.io/is-default-class': 'true'
                    }),
                _make_sc(
                    'default-b',
                    annotations={
                        'storageclass.kubernetes.io/is-default-class': 'true'
                    }),
            ]))
        volume_provision._apply_pvc_volume(config)
        mock_create.assert_called_once()


def test_apply_pvc_omitted_list_storage_class_api_error_propagates():
    """O5: K8s API error during default-check → distinguishable error msg."""
    config = _apply_config(storage_class_name=None)
    fake_api_exc = type('FakeApiException', (Exception,), {})
    with mock.patch.object(volume_provision, 'kubernetes') as mock_k8s, \
         mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        mock_k8s.api_exception.return_value = fake_api_exc
        mock_k8s.API_TIMEOUT = 5
        mock_k8s.storage_api.return_value.list_storage_class.side_effect = (
            fake_api_exc('rbac forbidden'))
        with pytest.raises(k8s_config.KubernetesError) as exc:
            volume_provision._apply_pvc_volume(config)
        msg = str(exc.value)
        # Must not be confused for the empty-default error.
        assert 'No storage class specified' not in msg
        assert 'Failed to list storage classes' in msg
        assert 'rbac forbidden' in msg
        mock_create.assert_not_called()
