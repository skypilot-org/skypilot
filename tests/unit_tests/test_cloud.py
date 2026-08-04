import pytest

from sky.clouds.cloud import Cloud
from sky.clouds.cloud import TeardownExecutionStrategy
from sky.utils import schemas


@pytest.mark.parametrize(("specific_reservations", "expected"), [({"a"}, {
    "a": 0
}), ((set(), {}))])
def test_cloud_get_reservations_available_resources(specific_reservations,
                                                    expected):

    available_resources = Cloud().get_reservations_available_resources(
        "instance_type", "region", "zone", specific_reservations)
    assert available_resources == expected


@pytest.mark.parametrize(
    ('remote_identity', 'expected_strategy'),
    [
        (schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value,
         TeardownExecutionStrategy.LEGACY_HEAD_CREDENTIALS),
        (schemas.RemoteIdentityOptions.NO_UPLOAD.value,
         TeardownExecutionStrategy.SERVER_ONLY),
        (schemas.RemoteIdentityOptions.SERVICE_ACCOUNT.value,
         TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
        ('validated-custom-identity',
         TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    ],
)
def test_cloud_teardown_execution_strategy_for_remote_identity(
        remote_identity, expected_strategy):
    assert (Cloud().get_teardown_execution_strategy(remote_identity) ==
            expected_strategy)
