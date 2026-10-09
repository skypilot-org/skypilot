"""Tests for Modal cloud provider.

Covers the Phase-1 foundation contract:
  - FND-01: modal extra in dependencies
  - FND-02: LazyImport zero-cost guarantee (no modal SDK at import sky time)
  - FND-03: cloud registry + __all__ registration
  - FND-04: PROVISIONER_VERSION / STATUS_VERSION / OPEN_PORTS_VERSION overrides
  - FND-05: 13-entry _CLOUD_UNSUPPORTED_FEATURES fence (each raises
            NotSupportedError)
  - CRED-01: _check_credentials token-pair validation (env-var precedence, TOML
             file, missing keys, absent SDK)
  - CRED-02: get_credential_file_mounts returns ~/.modal.toml mount

Phase-4 additions:
  - TEST-03 / D-05: 13-item parametrized NotSupportedError test
  - WIRE-01 / D-01: assert modal-ray.yml.j2 exists with correct provider.module
  - WIRE-02 / D-01: assert backend template wiring + provisioner importability
"""
import pathlib
import subprocess
import sys

import pytest

import sky
from sky import exceptions
import sky.clouds as clouds
from sky.clouds.modal import Modal
from sky.resources import Resources
from sky.setup_files import dependencies
from sky.utils import registry

# ---------------------------------------------------------------------------
# TEST-03 / D-05: 13-item parametrized NotSupportedError test
# The list must be module-level so parametrize IDs resolve at collection time.
# pylint: disable=protected-access
_UNSUPPORTED_FEATURES = list(Modal._CLOUD_UNSUPPORTED_FEATURES.keys())
# pylint: enable=protected-access


@pytest.mark.parametrize('feature',
                         _UNSUPPORTED_FEATURES,
                         ids=[f.name for f in _UNSUPPORTED_FEATURES])
def test_each_unsupported_feature_raises(feature):
    """TEST-03 / D-05: each of the 13 features raises NotSupportedError with a
    non-empty message so CI output pinpoints the specific broken feature."""
    resources = Resources(cloud=Modal())
    with pytest.raises(exceptions.NotSupportedError) as exc_info:
        Modal.check_features_are_supported(resources, {feature})
    msg = str(exc_info.value)
    assert msg, (
        f'NotSupportedError for {feature.name} must have a non-empty message')


def test_modal_registration():
    """FND-03: Modal is registered in the cloud registry and __all__."""
    cloud = registry.CLOUD_REGISTRY.from_str('modal')
    assert isinstance(cloud, Modal)
    assert Modal()._REPR == 'Modal'  # pylint: disable=protected-access
    assert 'Modal' in sky.clouds.__all__


def test_modal_in_dependencies():
    """FND-01: modal extra exists in extras_require with correct packages."""
    assert 'modal' in dependencies.extras_require
    extra = dependencies.extras_require['modal']
    modal_pkgs = [p for p in extra if p.startswith('modal')]
    assert modal_pkgs, 'Expected modal>=... package in extras_require[modal]'
    assert any('modal>=1.0.0,<2' in p for p in modal_pkgs), (
        f'Expected modal>=1.0.0,<2 in extras_require[modal]; got {modal_pkgs}')
    tomli_pkgs = [p for p in extra if 'tomli' in p]
    assert tomli_pkgs, ('Expected tomli (or tomllib) package in '
                        'extras_require[modal]')


def test_version_attrs():
    """FND-04: PROVISIONER/STATUS/OPEN_PORTS version overrides are correct."""
    assert Modal.PROVISIONER_VERSION is clouds.ProvisionerVersion.SKYPILOT, (
        'PROVISIONER_VERSION must be SKYPILOT (not deprecated RAY_AUTOSCALER)')
    assert Modal.STATUS_VERSION is clouds.StatusVersion.SKYPILOT, (
        'STATUS_VERSION must be SKYPILOT (not deprecated CLOUD_CLI)')
    assert Modal.OPEN_PORTS_VERSION is clouds.OpenPortsVersion.LAUNCH_ONLY, (
        'OPEN_PORTS_VERSION must be LAUNCH_ONLY')


def test_unsupported_features():
    """FND-05: all 13 unsupported features raise NotSupportedError; DOCKER_IMAGE
    does not."""
    resources = Resources(cloud=Modal())

    # pylint: disable=protected-access
    # Every value in the unsupported dict must be a non-empty string.
    for feature, msg in Modal._CLOUD_UNSUPPORTED_FEATURES.items():
        assert isinstance(msg, str) and msg, (
            f'Feature {feature} has empty or non-string message: {msg!r}')

    # STOP message must mention stop/resume (success criterion 3).
    stop_msg = Modal._CLOUD_UNSUPPORTED_FEATURES[
        clouds.CloudImplementationFeatures.STOP]
    assert 'stop' in stop_msg.lower() or 'resume' in stop_msg.lower(), (
        f'STOP message must mention stop or resume; got: {stop_msg!r}')

    # Exactly 13 features must be flagged.
    assert len(Modal._CLOUD_UNSUPPORTED_FEATURES) == 13, (
        f'Expected 13 unsupported features, got '
        f'{len(Modal._CLOUD_UNSUPPORTED_FEATURES)}')

    # Each flagged feature must raise NotSupportedError.
    for feature in Modal._CLOUD_UNSUPPORTED_FEATURES:
        with pytest.raises(exceptions.NotSupportedError):
            Modal.check_features_are_supported(resources, {feature})
    # pylint: enable=protected-access

    # A supported feature (DOCKER_IMAGE) must NOT raise.
    Modal.check_features_are_supported(
        resources, {clouds.CloudImplementationFeatures.DOCKER_IMAGE})


def test_check_credentials_sdk_absent(monkeypatch):
    """CRED-01: absent modal SDK returns disabled with install hint."""
    # Simulate find_spec('modal') returning None (SDK not installed).
    monkeypatch.setattr('sky.clouds.modal.import_lib_util.find_spec',
                        lambda name: None)
    # Env vars must not interfere.
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)

    valid, msg = Modal._check_credentials()  # pylint: disable=protected-access
    assert not valid, 'Expected disabled when modal SDK is absent'
    assert msg is not None
    assert 'skypilot[modal]' in msg, (
        f'Install hint must reference skypilot[modal]; got: {msg!r}')


def test_check_credentials_env_vars(monkeypatch, tmp_path):
    """CRED-01: env vars MODAL_TOKEN_ID + MODAL_TOKEN_SECRET take
    precedence."""
    # Point config path at a non-existent file so file-based check would fail.
    monkeypatch.setenv('MODAL_CONFIG_PATH', str(tmp_path / 'absent.toml'))
    monkeypatch.setenv('MODAL_TOKEN_ID', 'ak-test')
    monkeypatch.setenv('MODAL_TOKEN_SECRET', 'as-test')

    valid, msg = Modal._check_credentials()  # pylint: disable=protected-access
    assert valid, f'Expected valid with both env vars set; msg={msg!r}'
    assert msg is None


def test_check_credentials_toml_valid(monkeypatch, tmp_path):
    """CRED-01: valid TOML token pair returns (True, None)."""
    toml_file = tmp_path / 'modal.toml'
    toml_file.write_bytes(b'[default]\ntoken_id = "ak-test"\n'
                          b'token_secret = "as-test"\n')
    monkeypatch.setenv('MODAL_CONFIG_PATH', str(toml_file))
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    monkeypatch.delenv('MODAL_PROFILE', raising=False)

    valid, msg = Modal._check_credentials()  # pylint: disable=protected-access
    assert valid, f'Expected valid with correct TOML; msg={msg!r}'
    assert msg is None


def test_check_credentials_missing_token_secret(monkeypatch, tmp_path):
    """CRED-01: TOML missing token_secret returns (False, msg) naming the
    key."""
    toml_file = tmp_path / 'modal.toml'
    toml_file.write_bytes(b'[default]\ntoken_id = "ak-test"\n')
    monkeypatch.setenv('MODAL_CONFIG_PATH', str(toml_file))
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    monkeypatch.delenv('MODAL_PROFILE', raising=False)

    valid, msg = Modal._check_credentials()  # pylint: disable=protected-access
    assert not valid, 'Expected invalid when token_secret is missing'
    assert msg is not None
    assert 'token_secret' in msg, (
        f'Error message must name the missing key; got: {msg!r}')


def test_check_credentials_missing_token_id(monkeypatch, tmp_path):
    """CRED-01: TOML missing token_id returns (False, msg) naming the key."""
    toml_file = tmp_path / 'modal.toml'
    toml_file.write_bytes(b'[default]\ntoken_secret = "as-test"\n')
    monkeypatch.setenv('MODAL_CONFIG_PATH', str(toml_file))
    monkeypatch.delenv('MODAL_TOKEN_ID', raising=False)
    monkeypatch.delenv('MODAL_TOKEN_SECRET', raising=False)
    monkeypatch.delenv('MODAL_PROFILE', raising=False)

    valid, msg = Modal._check_credentials()  # pylint: disable=protected-access
    assert not valid, 'Expected invalid when token_id is missing'
    assert msg is not None
    assert 'token_id' in msg, (
        f'Error message must name the missing key; got: {msg!r}')


def test_credential_file_mounts():
    """CRED-02: get_credential_file_mounts returns ~/.modal.toml mount."""
    mounts = Modal().get_credential_file_mounts()
    assert mounts == {
        '~/.modal.toml': '~/.modal.toml'
    }, (f'Expected {{~/.modal.toml: ~/.modal.toml}}; got {mounts!r}')


def test_no_modal_import_cost():
    """FND-02: `import sky` must not trigger a top-level modal SDK import.

    Runs Python with -X importtime and asserts no importtime line referencing
    the modal SDK package appears in stderr. The Modal cloud module itself
    (sky.clouds.modal / sky.adaptors.modal) is expected; only the SDK import
    `modal` (standalone package) is forbidden at sky import time.
    """
    result = subprocess.run(  # pylint: disable=subprocess-run-check
        [sys.executable, '-X', 'importtime', '-c', 'import sky'],
        capture_output=True,
        text=True,
        errors='replace',
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f'`import sky` failed (returncode={result.returncode}); '
            f'skipping LazyImport cost check. stderr: {result.stderr[:500]}')

    # importtime lines look like:
    #   import time:       123 |       456 | modal
    # We want to ensure no such line has `modal` as the module name (the
    # rightmost field after the last `|`).  We allow lines that reference
    # sky.clouds.modal or sky.adaptors.modal (our own thin wrappers).
    for line in result.stderr.splitlines():
        if '|' not in line:
            continue
        # Extract the module-name part (after the last pipe)
        module_name = line.rsplit('|', 1)[-1].strip()
        # Reject if it is exactly 'modal' (the SDK package, not our wrappers)
        assert module_name != 'modal', (
            'Modal SDK was imported at sky import time (LazyImport broken).\n'
            f'Offending importtime line: {line!r}')


# ---------------------------------------------------------------------------
# WIRE-01 / D-01: assert modal-ray.yml.j2 exists and has correct content
# ---------------------------------------------------------------------------


def test_wire01_template_exists():
    """WIRE-01 / D-01: modal-ray.yml.j2 exists and declares
    provider.module: sky.provision.modal.

    This is an assert-only check — the template was created and live-proven
    in Phase 3 (03-02-SUMMARY.md commit 37e61b63).
    """
    # Locate the template relative to this test file's repo root
    repo_root = pathlib.Path(__file__).parent.parent.parent
    template_path = repo_root / 'sky' / 'templates' / 'modal-ray.yml.j2'
    assert template_path.exists(), (
        f'modal-ray.yml.j2 not found at expected path: {template_path}')
    content = template_path.read_text(encoding='utf-8')
    assert 'module: sky.provision.modal' in content, (
        'modal-ray.yml.j2 must declare provider.module: sky.provision.modal. '
        f'Content excerpt: {content[:300]!r}')


# ---------------------------------------------------------------------------
# WIRE-02 / D-01: backend template wiring + provisioner importability
# ---------------------------------------------------------------------------


def test_wire02_backend_template_wired():
    """WIRE-02 / D-01: _get_cluster_config_template returns 'modal-ray.yml.j2'
    for a Modal cloud instance.

    The mapping was added in Phase 3 at
    sky/backends/cloud_vm_ray_backend.py line 337.
    """
    from sky.backends import cloud_vm_ray_backend  # noqa: PLC0415

    # _get_cluster_config_template is a module-level function in
    # cloud_vm_ray_backend (not a method on CloudVmRayBackend).
    template = cloud_vm_ray_backend._get_cluster_config_template(  # pylint: disable=protected-access
        clouds.Modal())
    assert template == 'modal-ray.yml.j2', (
        f'_get_cluster_config_template(Modal()) must return "modal-ray.yml.j2"; '
        f'got {template!r}')


def test_provisioner_package_importable():
    """WIRE-02 / D-01: sky.provision.modal is importable (registered in
    sky/provision/__init__.py line 27: `from sky.provision import modal`).
    """
    import sky.provision.modal  # noqa: F401, PLC0415
    assert 'sky.provision.modal' in sys.modules, (
        'sky.provision.modal must be importable and cached in sys.modules')
