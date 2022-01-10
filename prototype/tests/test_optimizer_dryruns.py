import pytest

import sky
from sky import clouds
from sky import exceptions


# Monkey-patching is required because in the test environment, no cloud is
# enabled. The optimizer checks the environment to find enabled clouds, and
# only generates plans within these clouds. The tests assume that all three
# clouds are enabled, so we monkeypatch the `sky.global_user_state` module
# to return all three clouds. We also monkeypatch `sky.init.init` so that
# when the optimizer tries calling it to update enabled_clouds, it does not
# raise SystemExit.
def _test_resources(monkeypatch, resources, enabled_clouds=clouds.ALL_CLOUDS):
    monkeypatch.setattr(
        'sky.global_user_state.get_enabled_clouds',
        lambda: enabled_clouds,
    )
    monkeypatch.setattr('sky.init.init', lambda *_args, **_kwargs: None)
    with sky.Dag() as dag:
        task = sky.Task('test_task')
        task.set_resources({resources})
    sky.run(dag, dryrun=True)
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


def test_clouds_not_enabled(monkeypatch):
    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources(monkeypatch,
                        sky.Resources(clouds.AWS()),
                        enabled_clouds=[
                            clouds.Azure(),
                            clouds.GCP(),
                        ])

    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources(monkeypatch,
                        sky.Resources(clouds.Azure()),
                        enabled_clouds=[clouds.AWS()])

    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources(monkeypatch,
                        sky.Resources(clouds.GCP()),
                        enabled_clouds=[clouds.AWS()])
