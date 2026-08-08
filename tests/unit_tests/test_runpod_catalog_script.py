import csv
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("runpod")

from sky.catalog.data_fetchers import fetch_runpod


SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "refresh-runpod-catalog.py"


@pytest.fixture
def catalog_script():
    module_spec = importlib.util.spec_from_file_location("refresh_runpod_catalog", SCRIPT_PATH)
    assert module_spec is not None
    assert module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def _write_catalog(path: Path, *, gpu_memory_mib: int = 40960) -> None:
    fieldnames = list(fetch_runpod.USEFUL_COLUMNS)
    row = {field: "" for field in fieldnames}
    row.update(
        AcceleratorName="A100",
        GpuInfo=str({"Gpus": [{"MemoryInfo": {"SizeInMiB": gpu_memory_mib}}]}),
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)


def test_validate_catalog_accepts_positive_gpu_memory(catalog_script, tmp_path):
    catalog_path = tmp_path / "vms.csv"
    _write_catalog(catalog_path)

    catalog_script.validate_catalog(catalog_path)


def test_validate_catalog_rejects_non_positive_gpu_memory(catalog_script, tmp_path):
    catalog_path = tmp_path / "vms.csv"
    _write_catalog(catalog_path, gpu_memory_mib=0)

    with pytest.raises(ValueError, match="positive GPU memory"):
        catalog_script.validate_catalog(catalog_path)


def test_catalog_is_fresh_only_for_valid_recent_file(catalog_script, monkeypatch, tmp_path):
    catalog_path = tmp_path / "vms.csv"
    _write_catalog(catalog_path)
    monkeypatch.setenv("RUNPOD_CATALOG_MAX_AGE_SECONDS", "1800")

    assert catalog_script.catalog_is_fresh(catalog_path)

    _write_catalog(catalog_path, gpu_memory_mib=0)
    assert not catalog_script.catalog_is_fresh(catalog_path)


def test_refresh_catalog_replaces_staged_file_after_validation(catalog_script, monkeypatch, tmp_path):
    catalog_path = tmp_path / "runpod" / "vms.csv"
    monkeypatch.setattr(catalog_script.catalog_common, "get_catalog_path", lambda _name: str(catalog_path))
    monkeypatch.setattr(catalog_script, "try_install_gpu_details_cache", lambda: None)
    monkeypatch.setattr(catalog_script.fetch_runpod, "fetch_runpod_catalog", lambda **_kwargs: object())
    monkeypatch.setattr(
        catalog_script.fetch_runpod,
        "save_catalog",
        lambda _instances, output_path: _write_catalog(Path(output_path)),
    )

    catalog_script.refresh_catalog()

    assert catalog_path.is_file()
    catalog_script.validate_catalog(catalog_path)


def test_refresh_catalog_uses_valid_existing_catalog_after_fetch_failure(
    catalog_script, monkeypatch, tmp_path
):
    catalog_path = tmp_path / "runpod" / "vms.csv"
    catalog_path.parent.mkdir()
    _write_catalog(catalog_path)
    original_contents = catalog_path.read_text(encoding="utf-8")
    monkeypatch.setattr(catalog_script.catalog_common, "get_catalog_path", lambda _name: str(catalog_path))
    monkeypatch.setattr(catalog_script, "try_install_gpu_details_cache", lambda: None)

    def fail_fetch(**_kwargs):
        raise RuntimeError("RunPod unavailable")

    monkeypatch.setattr(catalog_script.fetch_runpod, "fetch_runpod_catalog", fail_fetch)

    catalog_script.refresh_catalog()

    assert catalog_path.read_text(encoding="utf-8") == original_contents


def test_refresh_catalog_fails_without_valid_cache(catalog_script, monkeypatch, tmp_path):
    catalog_path = tmp_path / "runpod" / "vms.csv"
    monkeypatch.setattr(catalog_script.catalog_common, "get_catalog_path", lambda _name: str(catalog_path))
    monkeypatch.setattr(catalog_script, "try_install_gpu_details_cache", lambda: None)

    def fail_fetch(**_kwargs):
        raise RuntimeError("RunPod unavailable")

    monkeypatch.setattr(catalog_script.fetch_runpod, "fetch_runpod_catalog", fail_fetch)

    with pytest.raises(RuntimeError, match="no valid existing catalog"):
        catalog_script.refresh_catalog()
