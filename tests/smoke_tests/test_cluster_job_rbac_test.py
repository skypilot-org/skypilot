"""Test for RBAC 409 race condition fix.

This test verifies that launching a cluster succeeds when RBAC resources
(ServiceAccount, Role, RoleBinding) already exist in the cluster.

Without the fix, this would fail with:
"roles.rbac.authorization.k8s.io 'skypilot-service-account-role' already exists"

To run:
    pytest tests/smoke_tests/test_cluster_job_rbac_test.py -v
"""

import pytest
from smoke_tests import smoke_tests_utils


@pytest.mark.kubernetes
def test_launch_cluster_with_preexisting_rbac():
    """Test that launching a cluster succeeds when RBAC resources already exist.

    This tests the fix for the 409 Conflict error that occurs when:
    1. A previous cluster launch created RBAC resources (Role, RoleBinding, ServiceAccount)
    2. A new cluster launch tries to create the same resources

    Without the fix, this would fail with:
    "roles.rbac.authorization.k8s.io 'skypilot-service-account-role' already exists"
    """
    name = smoke_tests_utils.get_cluster_name()

    # First, manually create the RBAC resources that SkyPilot would create.
    # This simulates a previous cluster launch that left stale RBAC resources.
    create_rbac_cmd = '''
        kubectl create serviceaccount skypilot-service-account -n default || true
        kubectl create role skypilot-service-account-role --verb=* --resource=* -n default || true
        kubectl create rolebinding skypilot-service-account-role-binding --role=skypilot-service-account-role --serviceaccount=default:skypilot-service-account -n default || true
    '''

    test = smoke_tests_utils.Test(
        'launch_cluster_with_preexisting_rbac',
        [
            # Create RBAC resources first (simulating previous run)
            create_rbac_cmd,
            # Now launch a cluster - this should succeed even with existing RBAC
            f'sky launch -y -c {name} --infra kubernetes --cpus 1 --memory 1GB',
            # Verify cluster is up
            f'sky status {name}',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)
