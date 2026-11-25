"""Tests for sky.provision.kubernetes.volume delete + apply behavior."""
from types import SimpleNamespace
from unittest import mock

import pytest

from sky import exceptions
from sky import models
from sky.adaptors import kubernetes
from sky.provision.kubernetes import config as k8s_config
from sky.provision.kubernetes import reads
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


def _read_error(status=None) -> exceptions.KubeAPIUnreachableError:
    """A failed read; `status` is what the API server answered, if anything."""
    error = exceptions.KubeAPIUnreachableError(f'Kubernetes API error {status}')
    if status is not None:
        error.__cause__ = kubernetes.api_exception()(status=status,
                                                     reason='Error')
    return error


class _FakeStorageClasses:
    """Answers the storage class reads with `classes`, or raises `error`."""

    def __init__(self, classes=(), error=None):
        self.classes = list(classes)
        self.error = error
        self.list_calls = []
        self.get_calls = []

    def install(self, monkeypatch) -> '_FakeStorageClasses':
        monkeypatch.setattr(reads, 'list_objects', self.list_objects)
        monkeypatch.setattr(reads, 'get_object', self.get_object)
        return self

    def list_objects(self, context, resource, **kwargs):
        assert resource == reads.STORAGE_CLASSES
        self.list_calls.append(dict(kwargs, context=context))
        if self.error is not None:
            raise self.error
        return self._result(self.classes)

    def get_object(self, context, resource, name, **kwargs):
        assert resource == reads.STORAGE_CLASSES
        self.get_calls.append(dict(kwargs, context=context, name=name))
        if self.error is not None:
            raise self.error
        return self._result(
            [sc for sc in self.classes if sc.metadata.name == name])

    @staticmethod
    def _result(items) -> reads.ReadResult:
        return reads.ReadResult(items=items,
                                resource_version='',
                                staleness_seconds=0,
                                reason=None)


def test_apply_pvc_explicit_storage_class_uses_read_storage_class(monkeypatch):
    """Explicit storage_class_name → per-name validation runs, list does not."""
    config = _apply_config(storage_class_name='premium-rwx')
    fake = _FakeStorageClasses([_make_sc('premium-rwx')]).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        volume_provision._apply_pvc_volume(config)
    # The pre-flight is the per-name check, against the API server.
    assert [(c['name'], c['consistency'], c['attempts']) for c in fake.get_calls
           ] == [('premium-rwx', reads.FRESH, 1)]
    # The empty-default branch must not have run.
    assert not fake.list_calls
    mock_create.assert_called_once()


def test_apply_pvc_omitted_storage_class_succeeds_with_default_present(
        monkeypatch):
    """Omitted storage_class_name + cluster has a default → proceeds."""
    config = _apply_config(storage_class_name=None)
    fake = _FakeStorageClasses([
        _make_sc('std', annotations={}),
        _make_sc('default-sc',
                 annotations={
                     'storageclass.kubernetes.io/is-default-class': 'true'
                 }),
    ]).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        volume_provision._apply_pvc_volume(config)
    assert [
        (c['context'], c['consistency'], c['attempts']) for c in fake.list_calls
    ] == [('my-context', reads.FRESH, 1)]
    # No per-name validation when nothing was specified.
    assert not fake.get_calls
    mock_create.assert_called_once()


def test_apply_pvc_omitted_storage_class_legacy_beta_annotation_recognized(
        monkeypatch):
    """Legacy beta annotation `storageclass.beta.kubernetes.io/is-default-class`
    is also accepted as marking the default StorageClass."""
    config = _apply_config(storage_class_name=None)
    legacy_annotation = ('storageclass.beta.kubernetes.io/is-default-class')
    _FakeStorageClasses([
        _make_sc('legacy-default', annotations={legacy_annotation: 'true'}),
    ]).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        volume_provision._apply_pvc_volume(config)
    mock_create.assert_called_once()


def test_apply_pvc_omitted_storage_class_capitalized_truthy_recognized(
        monkeypatch):
    """K8s admission accepts True/TRUE/1/t/T as truthy — we must too.

    Strict equality on `'true'` would miss `'True'`-annotated defaults
    emitted by some Helm charts in the wild.
    """
    default_annotation = 'storageclass.kubernetes.io/is-default-class'
    for truthy in ('True', 'TRUE', '1', 't', 'T'):
        config = _apply_config(storage_class_name=None)
        _FakeStorageClasses([
            _make_sc(f'sc-{truthy}', annotations={default_annotation: truthy}),
        ]).install(monkeypatch)
        with mock.patch.object(volume_provision,
                               'create_persistent_volume_claim') as mock_create:
            volume_provision._apply_pvc_volume(config)
            assert mock_create.call_count == 1, (
                f'expected {truthy!r} to be treated as a default annotation')


def test_apply_pvc_omitted_storage_class_no_default_raises(monkeypatch):
    """Omitted + no default-annotated SC → KubernetesError; PVC not created."""
    config = _apply_config(storage_class_name=None)
    _FakeStorageClasses([
        _make_sc('std', annotations={}),
        # 'false' annotation must NOT be treated as default.
        _make_sc('not-default',
                 annotations={
                     'storageclass.kubernetes.io/is-default-class': 'false'
                 }),
    ]).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        with pytest.raises(k8s_config.KubernetesError) as exc:
            volume_provision._apply_pvc_volume(config)
        msg = str(exc.value)
        assert 'No storage class specified' in msg
        assert repr('my-context') in msg
        assert 'config.storage_class_name' in msg
        mock_create.assert_not_called()


def test_apply_pvc_omitted_multiple_defaults_proceeds(monkeypatch):
    """Multiple SCs annotated default → proceeds (K8s picks one).

    We don't reject this server-side. CLI/SDK callers behave consistently
    with K8s itself (which picks one nondeterministically and binds the
    PVC); the dashboard separately warns on multi-default clusters.
    """
    config = _apply_config(storage_class_name=None)
    _FakeStorageClasses([
        _make_sc('default-a',
                 annotations={
                     'storageclass.kubernetes.io/is-default-class': 'true'
                 }),
        _make_sc('default-b',
                 annotations={
                     'storageclass.kubernetes.io/is-default-class': 'true'
                 }),
    ]).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        volume_provision._apply_pvc_volume(config)
        mock_create.assert_called_once()


def test_apply_pvc_omitted_list_storage_class_api_error_propagates(monkeypatch):
    """Non-RBAC K8s API error during default-check → distinguishable error msg.

    Uses status=500 to exercise the non-RBAC path (RBAC 401/403 is
    handled separately by the permission-tolerance tests below).
    """
    config = _apply_config(storage_class_name=None)
    _FakeStorageClasses(error=_read_error(500)).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        with pytest.raises(k8s_config.KubernetesError) as exc:
            volume_provision._apply_pvc_volume(config)
        msg = str(exc.value)
        # Must not be confused for the empty-default error.
        assert 'No storage class specified' not in msg
        assert 'Failed to list storage classes' in msg
        assert 'Kubernetes API error 500' in msg
        mock_create.assert_not_called()


def test_apply_pvc_omitted_list_storage_class_not_served_raises(monkeypatch):
    """An API server that does not serve storage classes fails the check."""
    config = _apply_config(storage_class_name=None)
    _FakeStorageClasses(error=exceptions.KubernetesResourceNotServedError(
        'not served')).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        with pytest.raises(k8s_config.KubernetesError,
                           match='Failed to list storage classes'):
            volume_provision._apply_pvc_volume(config)
        mock_create.assert_not_called()


@pytest.mark.parametrize('storage_class_name', [None, 'premium-rwx'])
def test_apply_pvc_unreachable_api_server_propagates(monkeypatch,
                                                     storage_class_name):
    """No answer from the API server is neither tolerated nor reworded."""
    config = _apply_config(storage_class_name=storage_class_name)
    error = _read_error(None)
    _FakeStorageClasses(error=error).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        with pytest.raises(exceptions.KubeAPIUnreachableError) as exc:
            volume_provision._apply_pvc_volume(config)
        assert exc.value is error
        mock_create.assert_not_called()


# ── use_existing short-circuits all SC validation ────────────────────────


def test_apply_pvc_use_existing_skips_sc_validation(monkeypatch):
    """use_existing=True bypasses both SC checks.

    The existing PVC is looked up by name/label; no provisioning happens,
    so neither the explicit-name validation nor the empty-default safety
    net is relevant. Previously this branched into the empty-default
    check and spuriously failed on clusters with no default-annotated SC.
    """
    config = _apply_config(storage_class_name=None)
    config.config['use_existing'] = True
    fake = _FakeStorageClasses().install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        volume_provision._apply_pvc_volume(config)
        # Neither SC read was made.
        assert not fake.list_calls
        assert not fake.get_calls
        mock_create.assert_called_once()


def test_apply_pvc_use_existing_with_explicit_sc_also_skips_validation(
        monkeypatch):
    """use_existing=True + explicit storage_class → no validation either.

    The existing PVC already has its own SC binding; validating that the
    name still resolves on the cluster is pointless work.
    """
    config = _apply_config(storage_class_name='some-sc')
    config.config['use_existing'] = True
    fake = _FakeStorageClasses().install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        volume_provision._apply_pvc_volume(config)
        assert not fake.get_calls
        assert not fake.list_calls
        mock_create.assert_called_once()


# ── 401/403 RBAC tolerance on both SC API calls ──────────────────────────


@pytest.mark.parametrize('status', [401, 403])
def test_apply_pvc_explicit_sc_rbac_denial_tolerated(monkeypatch, status):
    """401/403 on the storage class read → warning + proceed.

    Namespace-constrained users on multi-tenant clusters often lack
    cluster-scoped `get storageclasses`. Block them and we regress
    flows that previously worked (K8s admission still binds the PVC).
    """
    config = _apply_config(storage_class_name='premium-rwx')
    _FakeStorageClasses(error=_read_error(status)).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        # Must not raise.
        volume_provision._apply_pvc_volume(config)
        mock_create.assert_called_once()


def test_apply_pvc_explicit_sc_404_still_raises(monkeypatch):
    """A storage class that does not exist still raises.

    The 401/403 tolerance must NOT swallow real validation errors. A
    missing storage class means the user specified a name that doesn't
    exist on the cluster, which is the legitimate failure mode the
    pre-existing check was designed to catch.
    """
    config = _apply_config(storage_class_name='typo-sc')
    _FakeStorageClasses([_make_sc('premium-rwx')]).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        with pytest.raises(k8s_config.KubernetesError) as exc:
            volume_provision._apply_pvc_volume(config)
        assert 'Check storage class typo-sc' in str(exc.value)
        mock_create.assert_not_called()


def test_apply_pvc_explicit_sc_api_error_raises(monkeypatch):
    """A non-RBAC error on the storage class read raises."""
    config = _apply_config(storage_class_name='premium-rwx')
    _FakeStorageClasses(error=_read_error(500)).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        with pytest.raises(k8s_config.KubernetesError,
                           match='Check storage class premium-rwx'):
            volume_provision._apply_pvc_volume(config)
        mock_create.assert_not_called()


@pytest.mark.parametrize('status', [401, 403])
def test_apply_pvc_omitted_sc_list_rbac_denial_tolerated(monkeypatch, status):
    """401/403 on the storage class list → warning + proceed.

    Namespace-constrained users may not have cluster-scoped
    `list storageclasses`. If a default actually exists, K8s admission
    will bind the PVC; if not, the user sees the same Pending behavior
    they'd have hit before this PR (no worse than master).
    """
    config = _apply_config(storage_class_name=None)
    _FakeStorageClasses(error=_read_error(status)).install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        volume_provision._apply_pvc_volume(config)
        mock_create.assert_called_once()


# ── storage_class_name == '': K8s "no class" / static-binding convention ─


def test_apply_pvc_empty_string_storage_class_skips_both_checks(monkeypatch):
    """storage_class_name='' is the K8s opt-out-of-dynamic-provisioning value.

    Used to bind a PVC to a manually-created PV (static provisioning).
    We must skip both the per-name validation and the empty-default
    safety net — the user explicitly chose "no class", so neither
    check is meaningful. K8s handles the binding semantics.
    """
    config = _apply_config(storage_class_name='')
    fake = _FakeStorageClasses().install(monkeypatch)
    with mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        volume_provision._apply_pvc_volume(config)
        # Neither SC read was made.
        assert not fake.get_calls
        assert not fake.list_calls
        mock_create.assert_called_once()


# ── default-context resolution: a volume with no context of its own ───────


def _patch_context_resolution(current_context, allowed_contexts):
    """Patch the two inputs the context resolution reads."""
    return (mock.patch.object(volume_provision.kubernetes_utils,
                              'get_current_kube_config_context_name',
                              return_value=current_context),
            mock.patch.object(volume_provision.clouds.Kubernetes,
                              'existing_allowed_contexts',
                              return_value=allowed_contexts))


def test_resolve_default_context_prefers_allowed_current_context():
    """The ordinary single-cluster case is unchanged: current-context wins."""
    patch_current, patch_allowed = _patch_context_resolution(
        'my-context', ['my-context', 'other-context'])
    with patch_current, patch_allowed:
        assert volume_provision._resolve_context_for_new_volume(
        ) == 'my-context'


def test_resolve_default_context_skips_disallowed_current_context():
    """current-context excluded by allowed_contexts → first allowed instead.

    Otherwise the PVC is created on a cluster that `allowed_contexts` keeps
    every pod off of, so nothing can ever mount it.
    """
    patch_current, patch_allowed = _patch_context_resolution(
        'not-allowed', ['first-allowed', 'second-allowed'])
    with patch_current, patch_allowed:
        assert volume_provision._resolve_context_for_new_volume(
        ) == 'first-allowed'


def test_resolve_default_context_refuses_when_no_context_is_allowed():
    """The kubeconfig has contexts, but the workspace allows none of them.

    Falling back to current-context would create the PVC where no pod of the
    workspace can mount it.
    """
    patch_current, patch_allowed = _patch_context_resolution('my-context', [])
    with patch_current, patch_allowed:
        with pytest.raises(ValueError, match='No Kubernetes context'):
            volume_provision._resolve_context_for_new_volume()


def test_resolve_default_context_falls_back_without_kubeconfig():
    """No kubeconfig at all → previous behavior; the K8s call reports it."""
    patch_current, patch_allowed = _patch_context_resolution(None, [])
    with patch_current, patch_allowed:
        assert volume_provision._resolve_context_for_new_volume() is None


def _region_less_config() -> models.VolumeConfig:
    return models.VolumeConfig(
        name='test-vol',
        type='k8s-pvc',
        cloud='kubernetes',
        region=None,
        zone=None,
        name_on_cloud='real-pvc',
        size='5',
        config={
            'namespace': 'default',
            'access_mode': 'ReadWriteOnce',
        },
    )


def test_get_context_namespace_creating_uses_allowed_context():
    """Creating a volume chooses a context, and records it on the config.

    Pinning `region` matters beyond this call: every later operation on the
    volume (mount, delete) reads it back instead of re-resolving.
    """
    config = _region_less_config()
    patch_current, patch_allowed = _patch_context_resolution(
        'not-allowed', ['first-allowed'])
    with patch_current, patch_allowed:
        context, namespace = volume_provision._get_context_namespace(
            config, creating=True)
    assert context == 'first-allowed'
    assert config.region == 'first-allowed'
    assert namespace == 'default'


def test_get_context_namespace_lookup_keeps_the_current_context():
    """Looking up a region-less volume must not re-decide where it lives.

    Volumes created before the region pinning above kept `region=None`, and
    `refresh_volume_config` repairs those only under in-cluster auth. Their
    PVC is on whatever the current context was when they were created, so a
    lookup that resolved through `allowed_contexts` instead would query the
    wrong cluster and report the volume deleted.
    """
    config = _region_less_config()
    patch_current, patch_allowed = _patch_context_resolution(
        'not-allowed', ['first-allowed'])
    with patch_current, patch_allowed:
        context, _ = volume_provision._get_context_namespace(config)
    assert context == 'not-allowed'
    assert config.region == 'not-allowed'


def test_apply_pvc_volume_creates_on_an_allowed_context(monkeypatch):
    """The apply path is the one that opts into choosing a context."""
    config = _region_less_config()
    patch_current, patch_allowed = _patch_context_resolution(
        'not-allowed', ['first-allowed'])
    fake = _FakeStorageClasses([
        _make_sc('default-sc',
                 annotations={
                     'storageclass.kubernetes.io/is-default-class': 'true'
                 }),
    ]).install(monkeypatch)
    with patch_current, patch_allowed, \
         mock.patch.object(volume_provision,
                           'create_persistent_volume_claim') as mock_create:
        volume_provision._apply_pvc_volume(config)
    assert config.region == 'first-allowed'
    assert [c['context'] for c in fake.list_calls] == ['first-allowed']
    mock_create.assert_called_once()


def test_get_context_namespace_keeps_explicit_region():
    """An explicit context is honored as-is: no resolution, no override."""
    config = _make_pvc_config(use_existing=False)
    with mock.patch.object(volume_provision,
                           '_resolve_context_for_new_volume') as mock_resolve:
        context, _ = volume_provision._get_context_namespace(config)
    mock_resolve.assert_not_called()
    assert context == 'my-context'
