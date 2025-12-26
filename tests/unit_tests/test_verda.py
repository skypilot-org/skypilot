"""Tests for Verda Cloud provider."""

from pathlib import Path
import tempfile

import pytest

from sky import clouds
from sky.clouds import verda


def test_verda_cloud_basics():
    cloud = verda.Verda()
    assert cloud.name == "verda"
    assert cloud._REPR == "Verda"
    assert cloud._MAX_CLUSTER_NAME_LEN_LIMIT == 120


def test_verda_credential_file_mounts():
    with tempfile.TemporaryDirectory() as tmpdir:
        cred_path = Path(tmpdir) / "config.yaml"
        cred_path.touch()
        with pytest.MonkeyPatch.context() as m:
            m.setattr(verda.Verda, "CREDENTIALS_PATH", str(cred_path))
            cloud = verda.Verda()
            mounts = cloud.get_credential_file_mounts()
            assert str(cred_path) in mounts
            assert mounts[str(cred_path)] == "~/.verda/config.json"


def test_verda_region_zone_validation_disallows_zones():
    cloud = verda.Verda()
    with pytest.raises(ValueError, match="does not support zones"):
        cloud.validate_region_zone("some-region", "zone-1")


def test_verda_check_credentials_missing(monkeypatch, tmp_path):
    cloud = verda.Verda()
    fake_path = tmp_path / "config.json"
    monkeypatch.setattr(verda.Verda, "CREDENTIALS_PATH", str(fake_path))
    valid, msg = cloud.check_credentials(clouds.CloudCapability.COMPUTE)
    assert not valid
    assert "Verda credentials not found" in msg
