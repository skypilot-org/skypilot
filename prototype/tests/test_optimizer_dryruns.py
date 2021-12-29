import pytest

import sky
from sky import clouds


def _test_resources(monkeypatch, resources):
    monkeypatch.setattr(
        'sky.global_user_state.get_enabled_clouds',
        lambda: sky.registry.ALL_CLOUDS,
    )
    with sky.Dag() as dag:
        task = sky.Task('test_task')
        task.set_resources({resources})
    sky.execute(dag, dryrun=True)
    assert True


def test_resources_aws(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(clouds.AWS(), 'p3.2xlarge'))


def test_resources_azure(monkeypatch):
    _test_resources(monkeypatch,
                    sky.Resources(clouds.Azure(), 'Standard_NC24s_v3'))


def test_resources_gcp(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(clouds.GCP(), 'n1-standard-16'))


def test_partial_k80(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(accelerators='K80'))


def test_partial_m60(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(accelerators='M60'))


def test_partial_p100(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(accelerators='P100'))


def test_partial_t4(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(accelerators='T4'))
    _test_resources(monkeypatch,
                    sky.Resources(accelerators={'T4': 8}, use_spot=True))


def test_partial_tpu(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(accelerators='tpu-v3-8'))


def test_partial_v100(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(clouds.AWS(),
                                               accelerators='V100'))
    _test_resources(
        monkeypatch,
        sky.Resources(clouds.AWS(), accelerators='V100', use_spot=True))
    _test_resources(monkeypatch,
                    sky.Resources(clouds.AWS(), accelerators={'V100': 8}))
