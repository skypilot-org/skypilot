import pytest
from smoke_tests import smoke_tests_utils


# ---------- Test launching a cluster that has pyproject.toml in the workdir ----------
@pytest.mark.parametrize(
    'image_id',
    [
        'docker:us-docker.pkg.dev/sky-dev-465/buildkite-test-images/test-workdir-pyproject:latest',
        'docker:us-docker.pkg.dev/sky-dev-465/buildkite-test-images/test-root-pyproject:latest',
    ])
def test_workdir_with_pyproject(generic_cloud: str, image_id: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'workdir_with_pyproject',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} --image-id {image_id}',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)
