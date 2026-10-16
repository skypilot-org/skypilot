"""Unit tests for generated cloud storage COPY commands."""

import json
import os
import pathlib
import subprocess
from typing import Any, Optional

import pytest

from sky import cloud_stores


def _write_executable(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding='utf-8')
    path.chmod(0o755)


def _run_gcs_copy_command(
    tmp_path: pathlib.Path,
    monkeypatch,
    credentials: Optional[Any] = None,
    *,
    raw_credentials: Optional[str] = None,
    gcloud_exit_code: int = 0,
    platform: str = 'Linux',
) -> tuple[str, pathlib.Path]:
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log_path = tmp_path / 'commands.log'
    credential_dir = tmp_path / 'credential files'
    credential_dir.mkdir()
    credential_path = credential_dir / 'adc.json'
    if raw_credentials is not None:
        credential_path.write_text(raw_credentials, encoding='utf-8')
    elif credentials is not None:
        credential_path.write_text(json.dumps(credentials), encoding='utf-8')

    _write_executable(
        bin_dir / 'uname',
        '#!/bin/bash\n'
        f'printf "%s\\n" "{platform}"\n',
    )
    _write_executable(
        bin_dir / 'gcloud',
        '#!/bin/bash\n'
        'printf "gcloud:%s\\n" "$*" >> "$SKYPILOT_TEST_LOG"\n'
        'exit "${SKYPILOT_TEST_GCLOUD_EXIT_CODE:-0}"\n',
    )
    _write_executable(
        bin_dir / 'gsutil',
        '#!/bin/bash\n'
        'printf "gsutil:%s\\n" "$*" >> "$SKYPILOT_TEST_LOG"\n'
        'printf "pass_credentials:%s\\n" '
        '"${CLOUDSDK_CORE_PASS_CREDENTIALS_TO_GSUTIL:-unset}" '
        '>> "$SKYPILOT_TEST_LOG"\n',
    )

    monkeypatch.setattr(cloud_stores.GcsCloudStorage, '_INSTALL_GSUTIL', 'true')
    env = os.environ.copy()
    env['PATH'] = f'{bin_dir}:{env["PATH"]}'
    env['GOOGLE_APPLICATION_CREDENTIALS'] = str(credential_path)
    env['SKYPILOT_TEST_LOG'] = str(log_path)
    env['SKYPILOT_TEST_GCLOUD_EXIT_CODE'] = str(gcloud_exit_code)

    command = cloud_stores.GcsCloudStorage().make_sync_file_command(
        'gs://private-bucket/object', '/tmp/object')
    subprocess.run(command,
                   shell=True,
                   check=True,
                   executable='/bin/bash',
                   env=env)
    return log_path.read_text(encoding='utf-8'), credential_path


def test_gcs_copy_uses_external_account_without_gcloud_activation(
        tmp_path, monkeypatch):
    log, credential_path = _run_gcs_copy_command(
        tmp_path,
        monkeypatch,
        {
            'type': 'external_account',
            'audience': '//iam.googleapis.com/projects/123/locations/global/'
                        'workloadIdentityPools/pool/providers/provider',
            'subject_token_type': 'urn:ietf:params:oauth:token-type:jwt',
            'token_url': 'https://sts.googleapis.com/v1/token',
            'credential_source': {
                'file': '/var/run/secrets/skypilot-gcp/token',
            },
        },
    )

    assert 'gcloud:' not in log
    assert f'Credentials:gs_external_account_file={credential_path}' in log
    assert 'pass_credentials:0' in log
    assert 'cp gs://private-bucket/object /tmp/object' in log


def test_gcs_copy_activates_service_account_from_configured_path(
        tmp_path, monkeypatch):
    log, credential_path = _run_gcs_copy_command(
        tmp_path,
        monkeypatch,
        {
            'type': 'service_account',
        },
    )

    assert 'gcloud:auth activate-service-account' in log
    assert f'--key-file={credential_path}' in log
    assert 'Credentials:gs_external_account_file=' not in log
    assert 'cp gs://private-bucket/object /tmp/object' in log


def test_gcs_copy_keeps_user_credential_fallback(tmp_path, monkeypatch):
    log, _ = _run_gcs_copy_command(
        tmp_path,
        monkeypatch,
        {
            'type': 'authorized_user',
            'client_id': 'client-id',
        },
        gcloud_exit_code=1,
    )

    assert 'gcloud:auth activate-service-account' in log
    assert 'Credentials:gs_external_account_file=' not in log
    assert 'cp gs://private-bucket/object /tmp/object' in log


@pytest.mark.parametrize(
    ('credentials', 'raw_credentials'),
    (
        (None, None),
        (None, '{invalid json'),
        (['external_account'], None),
    ),
)
def test_gcs_copy_falls_back_when_adc_is_not_external_account(
    tmp_path,
    monkeypatch,
    credentials,
    raw_credentials,
):
    log, credential_path = _run_gcs_copy_command(
        tmp_path,
        monkeypatch,
        credentials,
        raw_credentials=raw_credentials,
        gcloud_exit_code=1,
    )

    assert 'gcloud:auth activate-service-account' in log
    assert f'--key-file={credential_path}' in log
    assert 'Credentials:gs_external_account_file=' not in log
    assert 'cp gs://private-bucket/object /tmp/object' in log


@pytest.mark.parametrize(
    ('platform', 'has_multiprocessing_option'),
    (
        ('Linux', False),
        ('Darwin', True),
    ),
)
def test_external_account_copy_preserves_platform_gsutil_options(
    tmp_path,
    monkeypatch,
    platform,
    has_multiprocessing_option,
):
    log, _ = _run_gcs_copy_command(
        tmp_path,
        monkeypatch,
        {
            'type': 'external_account',
        },
        platform=platform,
    )

    assert 'gcloud:' not in log
    assert ('GSUtil:parallel_process_count=1'
            in log) is has_multiprocessing_option


def test_gcs_directory_copy_keeps_rsync_arguments(monkeypatch):
    monkeypatch.setattr(cloud_stores.GcsCloudStorage, '_INSTALL_GSUTIL', 'true')

    command = cloud_stores.GcsCloudStorage().make_sync_dir_command(
        'gs://private-bucket/prefix', '/tmp/prefix')

    assert 'rsync -e -r gs://private-bucket/prefix /tmp/prefix' in command
