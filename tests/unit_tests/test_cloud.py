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


class TestCloudEquality:
    """Cloud.__eq__/__hash__: value semantics over identity.

    Cloud objects are stateless, so two instances of the same class are
    the same cloud. Before __eq__/__hash__ existed, cloud-keyed dicts
    and `in` checks silently used identity, which broke any lookup where
    the probe instance differed from the key instance (see
    Optimizer._select_best_infra).
    """

    def test_same_class_instances_are_equal(self):
        from sky import clouds
        for cloud_cls in (clouds.AWS, clouds.GCP, clouds.Azure,
                          clouds.Kubernetes, clouds.Lambda):
            assert cloud_cls() == cloud_cls()
            assert hash(cloud_cls()) == hash(cloud_cls())

    def test_different_clouds_are_not_equal(self):
        from sky import clouds
        assert clouds.AWS() != clouds.GCP()
        assert clouds.Kubernetes() != clouds.AWS()

    def test_not_equal_to_non_cloud(self):
        from sky import clouds
        assert clouds.AWS() != 'AWS'
        assert clouds.AWS() != None  # pylint: disable=singleton-comparison

    def test_ssh_and_kubernetes_are_not_equal_either_direction(self):
        """SSH subclasses Kubernetes; equality must stay symmetric.

        is_same_cloud() keeps its historical isinstance semantics
        (asymmetric: k8s considers SSH the same *family*), which is why
        __eq__ is exact-type instead of delegating to it.
        """
        from sky import clouds
        ssh, k8s = clouds.SSH(), clouds.Kubernetes()
        assert ssh != k8s
        assert k8s != ssh
        # Family semantics preserved, unchanged by __eq__:
        assert k8s.is_same_cloud(ssh)
        assert not ssh.is_same_cloud(k8s)

    def test_dict_membership_across_instances(self):
        """The exact lookup shape that failed in _select_best_infra."""
        from sky import clouds
        candidates = {clouds.AWS(): ['res-a'], clouds.Kubernetes(): ['res-k']}
        assert clouds.AWS() in candidates
        assert clouds.Kubernetes() in candidates
        assert clouds.GCP() not in candidates
        assert candidates[clouds.AWS()] == ['res-a']

    def test_set_deduplicates_instances(self):
        from sky import clouds
        assert len({clouds.AWS(), clouds.AWS(), clouds.Kubernetes()}) == 2


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
