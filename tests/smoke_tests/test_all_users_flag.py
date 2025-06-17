# Smoke tests for SkyPilot -u/--all-users flag semantics
# Tests that without -u, operations only affect current user's resources
# and with -u, operations can affect other users' resources
#
# Example usage:
# > pytest tests/smoke_tests/test_all_users_flag.py

from typing import List

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky.skylet import constants


def set_user(user_id: str, user_name: str, commands: List[str]) -> List[str]:
    """Helper to set environment variables for multi-user testing."""
    return [
        f'export {constants.USER_ID_ENV_VAR}="{user_id}"; '
        f'export {constants.USER_ENV_VAR}="{user_name}"; ' + cmd
        for cmd in commands
    ]


@pytest.mark.no_hyperbolic  # Hyperbolic does not support multi-tenant
def test_cluster_operations_all_users_flag(generic_cloud: str):
    """Test that -u flag controls access to other users' clusters for stop/start operations."""
    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef01'
    user_1_name = 'test_user_1'
    user_2 = 'abcdef02'
    user_2_name = 'test_user_2'

    # Skip stop/start tests for Kubernetes as they're not supported
    if generic_cloud == 'kubernetes':
        pytest.skip("Kubernetes does not support stop/start operations")

    test = smoke_tests_utils.Test(
        'test_cluster_operations_all_users_flag',
        [
            'echo "==== Test cluster operations with -u flag ===="',
            
            # User 1 creates a cluster
            *set_user(user_1, user_1_name, [
                f'sky launch -y -c {name}-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
                f'sky status {name}-1 | grep UP',
            ]),
            
            # User 2 creates another cluster  
            *set_user(user_2, user_2_name, [
                f'sky launch -y -c {name}-2 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
                f'sky status {name}-2 | grep UP',
            ]),
            
            # Test stop without -u: User 2 cannot stop User 1's cluster
            *set_user(user_2, user_2_name, [
                f'sky stop -y {name}-1 2>&1 | grep "owned by other users" || echo "Should show hint about other users"',
                f'sky stop -y {name}-1 2>&1 | grep "all-users.*-u" || echo "Should suggest using -u flag"',
                # Verify User 1's cluster is still UP
                f'sky status {name}-1 | grep UP',
                # User 2 can stop their own cluster
                f'sky stop -y {name}-2',
                f'sky status {name}-2 | grep STOPPED',
            ]),
            
            # Test stop with -u: User 2 can stop User 1's cluster
            *set_user(user_2, user_2_name, [
                f'sky stop -y -u {name}-1',
                f'sky status {name}-1 | grep STOPPED',
            ]),
            
            # Test start without -u: User 2 cannot start User 1's cluster  
            *set_user(user_2, user_2_name, [
                f'sky start -y {name}-1 2>&1 | grep "owned by other users" || echo "Should show hint about other users"',
                f'sky start -y {name}-1 2>&1 | grep "all-users.*-u" || echo "Should suggest using -u flag"',
                # Verify User 1's cluster is still STOPPED
                f'sky status {name}-1 | grep STOPPED',
                # User 2 can start their own cluster
                f'sky start -y {name}-2',
                f'sky status {name}-2 | grep UP',
            ]),
            
            # Test start with -u: User 2 can start User 1's cluster
            *set_user(user_2, user_2_name, [
                f'sky start -y -u {name}-1',
                f'sky status {name}-1 | grep UP',
            ]),
            
            # Test autostop without -u: User 2 cannot autostop User 1's cluster
            *set_user(user_2, user_2_name, [
                f'sky autostop -y -i 1 {name}-1 2>&1 | grep "owned by other users" || echo "Should show hint about other users"',
                f'sky autostop -y -i 1 {name}-1 2>&1 | grep "all-users.*-u" || echo "Should suggest using -u flag"',
                # User 2 can autostop their own cluster
                f'sky autostop -y -i 10 {name}-2',
            ]),
            
            # Test autostop with -u: User 2 can autostop User 1's cluster
            *set_user(user_2, user_2_name, [
                f'sky autostop -y -u -i 10 {name}-1',
            ]),
        ],
        f'sky down -y {name}-1 {name}-2',
        timeout=smoke_tests_utils.get_timeout(generic_cloud) * 2,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_hyperbolic  # Hyperbolic does not support multi-tenant
def test_cluster_down_all_users_flag(generic_cloud: str):
    """Test that -u flag controls access to other users' clusters for down operations."""
    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef03'
    user_1_name = 'test_user_1'
    user_2 = 'abcdef04'
    user_2_name = 'test_user_2'

    test = smoke_tests_utils.Test(
        'test_cluster_down_all_users_flag',
        [
            'echo "==== Test cluster down with -u flag ===="',
            
            # User 1 creates a cluster
            *set_user(user_1, user_1_name, [
                f'sky launch -y -c {name}-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
                f'sky status {name}-1 | grep UP',
            ]),
            
            # User 2 creates another cluster  
            *set_user(user_2, user_2_name, [
                f'sky launch -y -c {name}-2 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
                f'sky status {name}-2 | grep UP',
            ]),
            
            # Test down without -u: User 2 cannot down User 1's cluster
            *set_user(user_2, user_2_name, [
                f'sky down -y {name}-1 2>&1 | grep "owned by other users" || echo "Should show hint about other users"',
                f'sky down -y {name}-1 2>&1 | grep "all-users.*-u" || echo "Should suggest using -u flag"',
                # Verify User 1's cluster is still UP
                f'sky status {name}-1 | grep UP',
                # User 2 can down their own cluster
                f'sky down -y {name}-2',
                # Verify User 2's cluster is gone
                f'sky status {name}-2 && exit 1 || echo "Expected: User 2 cluster should be down"',
            ]),
            
            # Test down with -u: User 2 can down User 1's cluster
            *set_user(user_2, user_2_name, [
                f'sky down -y -u {name}-1',
                # Verify User 1's cluster is gone
                f'sky status {name}-1 && exit 1 || echo "Expected: User 1 cluster should be down"',
            ]),
        ],
        f'sky down -y {name}-1 {name}-2',
        timeout=smoke_tests_utils.get_timeout(generic_cloud) * 2,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_hyperbolic  # Hyperbolic does not support multi-tenant  
def test_cluster_jobs_cancel_all_users_flag(generic_cloud: str):
    """Test that -u flag controls access to other users' cluster jobs for cancel operations."""
    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef05'
    user_1_name = 'test_user_1'
    user_2 = 'abcdef06'
    user_2_name = 'test_user_2'

    test = smoke_tests_utils.Test(
        'test_cluster_jobs_cancel_all_users_flag', 
        [
            'echo "==== Test cluster job cancel with -u flag ===="',
            
            # User 1 creates a cluster and starts a long-running job
            *set_user(user_1, user_1_name, [
                f'sky launch -y -c {name}-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
                f'sky exec {name}-1 -n job-1 -d "sleep 300"',
                # Wait for job to start
                'sleep 10',
                f'sky queue {name}-1 | grep job-1 | grep RUNNING',
            ]),
            
            # User 2 creates a cluster and starts a job
            *set_user(user_2, user_2_name, [
                f'sky launch -y -c {name}-2 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',  
                f'sky exec {name}-2 -n job-2 -d "sleep 300"',
                # Wait for job to start
                'sleep 10',
                f'sky queue {name}-2 | grep job-2 | grep RUNNING',
            ]),
            
            # Test cancel without -u: User 2 cannot cancel User 1's job by cluster name
            *set_user(user_2, user_2_name, [
                f'sky cancel -y {name}-1 2>&1 | grep "owned by other users" || echo "Should show hint about other users"',
                f'sky cancel -y {name}-1 2>&1 | grep "all-users.*-u" || echo "Should suggest using -u flag"',
                # Verify User 1's job is still running
                f'sky queue -u {name}-1 | grep job-1 | grep RUNNING',
                # User 2 can cancel jobs on their own cluster
                f'sky cancel -y {name}-2',
                f'sky queue {name}-2 | grep job-2 | grep CANCELLED',
            ]),
            
            # Test cancel with -u: User 2 can cancel User 1's job
            *set_user(user_2, user_2_name, [
                f'sky cancel -y -u {name}-1',
                f'sky queue -u {name}-1 | grep job-1 | grep CANCELLED',
            ]),
        ],
        f'sky down -y {name}-1 {name}-2',
        timeout=smoke_tests_utils.get_timeout(generic_cloud) * 2,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_hyperbolic  # Hyperbolic does not support multi-tenant
@pytest.mark.managed_jobs
def test_managed_jobs_cancel_all_users_flag(generic_cloud: str):
    """Test that -u flag controls access to other users' managed jobs for cancel operations."""
    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef07' 
    user_1_name = 'test_user_1'
    user_2 = 'abcdef08'
    user_2_name = 'test_user_2'

    test = smoke_tests_utils.Test(
        'test_managed_jobs_cancel_all_users_flag',
        [
            'echo "==== Test managed job cancel with -u flag ===="',
            
            # User 1 launches a long-running managed job
            *set_user(user_1, user_1_name, [
                f'sky jobs launch -n {name}-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y -d "sleep 300"',
                smoke_tests_utils.get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=f'{name}-1',
                    job_status=[
                        sky.ManagedJobStatus.PENDING,
                        sky.ManagedJobStatus.DEPRECATED_SUBMITTED, 
                        sky.ManagedJobStatus.STARTING,
                        sky.ManagedJobStatus.RUNNING
                    ],
                    timeout=120),
            ]),
            
            # User 2 launches another managed job
            *set_user(user_2, user_2_name, [
                f'sky jobs launch -n {name}-2 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y -d "sleep 300"',
                smoke_tests_utils.get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=f'{name}-2',
                    job_status=[
                        sky.ManagedJobStatus.PENDING,
                        sky.ManagedJobStatus.DEPRECATED_SUBMITTED,
                        sky.ManagedJobStatus.STARTING, 
                        sky.ManagedJobStatus.RUNNING
                    ],
                    timeout=120),
            ]),
            
            # Test cancel without -u: User 2 cannot cancel User 1's managed job by name
            *set_user(user_2, user_2_name, [
                f'sky jobs cancel -y -n {name}-1 2>&1 | grep "owned by other users" || echo "Expected: Cannot cancel other user\'s managed job without -u"',
                # Verify User 1's job is still running (check with -u to see all jobs)
                f'sky jobs queue -u | grep {name}-1 | grep "RUNNING\\|STARTING"',
                # User 2 can cancel their own managed job
                f'sky jobs cancel -y -n {name}-2', 
                f'sky jobs queue | grep {name}-2 | grep CANCELLED',
            ]),
            
            # Test cancel with -u: User 2 can cancel User 1's managed job
            *set_user(user_2, user_2_name, [
                f'sky jobs cancel -y -u -n {name}-1',
                f'sky jobs queue -u | grep {name}-1 | grep CANCELLED',
            ]),
        ],
        f'sky jobs cancel -y -n {name}-1; sky jobs cancel -y -n {name}-2',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=smoke_tests_utils.get_timeout(generic_cloud) * 3,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_hyperbolic  # Hyperbolic does not support multi-tenant
def test_status_and_queue_show_hints_for_other_users(generic_cloud: str):
    """Test that status/queue commands show helpful hints when resources owned by other users exist."""
    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef09'
    user_1_name = 'test_user_1' 
    user_2 = 'abcdef0a'
    user_2_name = 'test_user_2'

    test = smoke_tests_utils.Test(
        'test_status_and_queue_show_hints_for_other_users',
        [
            'echo "==== Test status/queue hints for other users ===="',
            
            # User 1 creates a cluster
            *set_user(user_1, user_1_name, [
                f'sky launch -y -c {name}-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
                f'sky status {name}-1 | grep UP',
            ]),
            
            # User 2 tries to access User 1's cluster and should see a hint
            *set_user(user_2, user_2_name, [
                f'sky status {name}-1 2>&1 | grep "owned by other users" || echo "Should show hint about other users"',
                f'sky status {name}-1 2>&1 | grep "all-users.*-u" || echo "Should suggest using -u flag"',
                # With -u flag, should be able to see the cluster
                f'sky status -u {name}-1 | grep UP',
            ]),
        ],
        f'sky down -y {name}-1',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_hyperbolic  # Hyperbolic does not support multi-tenant 
@pytest.mark.managed_jobs
def test_managed_jobs_queue_show_hints_for_other_users(generic_cloud: str):
    """Test that managed jobs queue shows hints when jobs owned by other users exist."""
    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef0b'
    user_1_name = 'test_user_1'
    user_2 = 'abcdef0c' 
    user_2_name = 'test_user_2'

    test = smoke_tests_utils.Test(
        'test_managed_jobs_queue_show_hints_for_other_users',
        [
            'echo "==== Test managed jobs queue hints for other users ===="',
            
            # User 1 launches a managed job
            *set_user(user_1, user_1_name, [
                f'sky jobs launch -n {name}-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/test_yamls/minimal.yaml',
                smoke_tests_utils.get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=f'{name}-1',
                    job_status=[sky.ManagedJobStatus.SUCCEEDED],
                    timeout=120),
            ]),
            
            # User 2 tries to access User 1's managed job and should see a hint
            *set_user(user_2, user_2_name, [
                # Try to cancel by name - should show hint
                f'sky jobs cancel -y -n {name}-1 2>&1 | grep "owned by other users" || echo "Should show hint about other users"',
                f'sky jobs cancel -y -n {name}-1 2>&1 | grep "all-users.*-u" || echo "Should suggest using -u flag"',
                # With -u flag, should be able to see and cancel the job
                f'sky jobs cancel -y -u -n {name}-1',
            ]),
        ],
        f'sky jobs cancel -y -n {name}-1',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=smoke_tests_utils.get_timeout(generic_cloud) * 2,
    )
    smoke_tests_utils.run_one_test(test) 
