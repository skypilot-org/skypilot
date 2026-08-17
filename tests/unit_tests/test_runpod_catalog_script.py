import csv
from pathlib import Path

import pytest

pytest.importorskip("runpod")

from sky.catalog import runpod_refresh
from sky.catalog.data_fetchers import fetch_runpod


def _write_catalog(path: Path, *, gpu_memory_mib: int = 40960) -> None:
    fieldnames = list(fetch_runpod.USEFUL_COLUMNS)
    row = {field: "" for field in fieldnames}
    row.update(
        AcceleratorName="A100",
        GpuInfo=str({"Gpus": [{
            "MemoryInfo": {
                "SizeInMiB": gpu_memory_mib
            }
        }]}),
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)


def test_validate_catalog_accepts_positive_gpu_memory(tmp_path):
    """Accept a catalog row containing positive GPU memory."""
    catalog_path = tmp_path / "vms.csv"
    _write_catalog(catalog_path)

    runpod_refresh.validate_catalog(catalog_path)


def test_validate_catalog_rejects_non_positive_gpu_memory(tmp_path):
    """Reject catalog rows whose serialized GPU memory is not positive."""
    catalog_path = tmp_path / "vms.csv"
    _write_catalog(catalog_path, gpu_memory_mib=0)

    with pytest.raises(ValueError, match="positive GPU memory"):
        runpod_refresh.validate_catalog(catalog_path)


def test_catalog_is_fresh_only_for_valid_recent_file(monkeypatch, tmp_path):
    """Treat only recent, schema-valid catalogs as reusable cache entries."""
    catalog_path = tmp_path / "vms.csv"
    _write_catalog(catalog_path)
    monkeypatch.setenv("RUNPOD_CATALOG_MAX_AGE_SECONDS", "1800")

    assert runpod_refresh.catalog_is_fresh(catalog_path)

    _write_catalog(catalog_path, gpu_memory_mib=0)
    assert not runpod_refresh.catalog_is_fresh(catalog_path)


def test_refresh_catalog_replaces_staged_file_after_validation(
        monkeypatch, tmp_path):
    """Install a validated refresh atomically at the catalog path."""
    fetcher = runpod_refresh._load_dependencies()[2]
    catalog_path = tmp_path / "runpod" / "vms.csv"
    monkeypatch.setattr(runpod_refresh.catalog_common, "get_catalog_path",
                        lambda _name: str(catalog_path))
    monkeypatch.setattr(runpod_refresh, "try_install_gpu_details_cache",
                        lambda: None)
    monkeypatch.setattr(fetcher, "fetch_runpod_catalog",
                        lambda **_kwargs: object())
    monkeypatch.setattr(
        fetcher,
        "save_catalog",
        lambda _instances, output_path: _write_catalog(Path(output_path)),
    )

    monkeypatch.setattr(runpod_refresh, "_configure_credentials", lambda: None)
    runpod_refresh.refresh_catalog()

    assert catalog_path.is_file()
    runpod_refresh.validate_catalog(catalog_path)


def test_refresh_catalog_uses_valid_existing_catalog_after_fetch_failure(
        monkeypatch, tmp_path):
    """Keep the last valid catalog when RunPod is temporarily unavailable."""
    fetcher = runpod_refresh._load_dependencies()[2]
    catalog_path = tmp_path / "runpod" / "vms.csv"
    catalog_path.parent.mkdir()
    _write_catalog(catalog_path)
    original_contents = catalog_path.read_text(encoding="utf-8")
    monkeypatch.setattr(runpod_refresh.catalog_common, "get_catalog_path",
                        lambda _name: str(catalog_path))
    monkeypatch.setattr(runpod_refresh, "try_install_gpu_details_cache",
                        lambda: None)

    def fail_fetch(**_kwargs):
        raise RuntimeError("RunPod unavailable")

    monkeypatch.setattr(fetcher, "fetch_runpod_catalog", fail_fetch)
    monkeypatch.setattr(runpod_refresh, "_configure_credentials", lambda: None)

    runpod_refresh.refresh_catalog()

    assert catalog_path.read_text(encoding="utf-8") == original_contents


def test_refresh_catalog_fails_without_valid_cache(monkeypatch, tmp_path):
    """Fail closed when refresh fails and no validated fallback exists."""
    fetcher = runpod_refresh._load_dependencies()[2]
    catalog_path = tmp_path / "runpod" / "vms.csv"
    monkeypatch.setattr(runpod_refresh.catalog_common, "get_catalog_path",
                        lambda _name: str(catalog_path))
    monkeypatch.setattr(runpod_refresh, "try_install_gpu_details_cache",
                        lambda: None)

    def fail_fetch(**_kwargs):
        raise RuntimeError("RunPod unavailable")

    monkeypatch.setattr(fetcher, "fetch_runpod_catalog", fail_fetch)
    monkeypatch.setattr(runpod_refresh, "_configure_credentials", lambda: None)

    with pytest.raises(RuntimeError, match="no valid existing catalog"):
        runpod_refresh.refresh_catalog()
