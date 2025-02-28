from typing import List

from smoke_tests import smoke_tests_utils

import sky
from sky.skylet import constants


def set_user(user_id: str, user_name: str, commands: List[str]) -> List[str]:
    return [
        f'export {constants.USER_ID_ENV_VAR}="{user_id}"; '
        f'export {constants.USER_ENV_VAR}="{user_name}"; ' + cmd
        for cmd in commands
    ]


# ---------- Test multi-tenant ----------
def test_multi_tenant(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef12'
    user_1_name = 'user1'
    user_2 = 'abcdef13'
    user_2_name = 'user2'

    stop_test_cmds = [
        'echo "==== Test multi-tenant cluster stop ===="',
        *set_user(
            user_2,
            user_2_name,
            [
                f'sky stop -y -a',
                # -a should only stop clusters from the current user.
                f's=$(sky status -u {name}-1) && echo "$s" && echo "$s" | grep {user_1_name} | grep UP',
                f's=$(sky status -u {name}-2) && echo "$s" && echo "$s" | grep {user_2_name} | grep STOPPED',
                # Explicit cluster name should stop the cluster.
                f'sky stop -y {name}-1',
                # Stopping cluster should not change the ownership of the cluster.
                f's=$(sky status) && echo "$s" && echo "$s" | grep {name}-1 && exit 1 || true',
                f'sky status {name}-1 | grep STOPPED',
                # Both clusters should be stopped.
                f'sky status -u | grep {name}-1 | grep STOPPED',
                f'sky status -u | grep {name}-2 | grep STOPPED',
            ]),
    ]
    if generic_cloud == 'kubernetes':
        # Skip the stop test for Kubernetes, as stopping is not supported.
        stop_test_cmds = []

    test = smoke_tests_utils.Test(
        'test_multi_tenant',
        [
            'echo "==== Test multi-tenant job on single cluster ===="',
            *set_user(user_1, user_1_name, [
                f'sky launch -y -c {name}-1 --cloud {generic_cloud} --cpus 2+ -n job-1 tests/test_yamls/minimal.yaml',
                f's=$(sky queue {name}-1) && echo "$s" && echo "$s" | grep job-1 | grep SUCCEEDED | awk \'{{print $1}}\' | grep 1',
                f's=$(sky queue -u {name}-1) && echo "$s" && echo "$s" | grep {user_1_name} | grep job-1 | grep SUCCEEDED',
            ]),
            *set_user(user_2, user_2_name, [
                f'sky exec {name}-1 -n job-2 \'echo "hello" && exit 1\'',
                f's=$(sky queue {name}-1) && echo "$s" && echo "$s" | grep job-2 | grep FAILED | awk \'{{print $1}}\' | grep 2',
                f's=$(sky queue {name}-1) && echo "$s" && echo "$s" | grep job-1 && exit 1 || true',
                f's=$(sky queue {name}-1 -u) && echo "$s" && echo "$s" | grep {user_2_name} | grep job-2 | grep FAILED',
                f's=$(sky queue {name}-1 -u) && echo "$s" && echo "$s" | grep {user_1_name} | grep job-1 | grep SUCCEEDED',
            ]),
            'echo "==== Test clusters from different users ===="',
            *set_user(
                user_2,
                user_2_name,
                [
                    f'sky launch -y -c {name}-2 --cloud {generic_cloud} --cpus 2+ -n job-3 tests/test_yamls/minimal.yaml',
                    f's=$(sky status {name}-2) && echo "$s" && echo "$s" | grep UP',
                    # sky status should not show other clusters from other users.
                    f's=$(sky status) && echo "$s" && echo "$s" | grep {name}-1 && exit 1 || true',
                    # Explicit cluster name should show the cluster.
                    f's=$(sky status {name}-1) && echo "$s" && echo "$s" | grep UP',
                    f's=$(sky status -u) && echo "$s" && echo "$s" | grep {user_2_name} | grep {name}-2 | grep UP',
                    f's=$(sky status -u) && echo "$s" && echo "$s" | grep {user_1_name} | grep {name}-1 | grep UP',
                ]),
            *stop_test_cmds,
            'echo "==== Test multi-tenant cluster down ===="',
            *set_user(
                user_2,
                user_2_name,
                [
                    f'sky down -y -a',
                    # STOPPED or UP based on whether we run the stop_test_cmds.
                    f'sky status -u | grep {name}-1 | grep "STOPPED\|UP"',
                    # Current user's clusters should be down'ed.
                    f'sky status -u | grep {name}-2 && exit 1 || true',
                    # Explicit cluster name should delete the cluster.
                    f'sky down -y {name}-1',
                    f'sky status | grep {name}-1 && exit 1 || true',
                ]),
        ],
        f'sky down -y {name}-1 {name}-2',
    )
    smoke_tests_utils.run_one_test(test)


def test_multi_tenant_managed_jobs(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef12'
    user_1_name = 'user1'
    user_2 = 'abcdef13'
    user_2_name = 'user2'

    test = smoke_tests_utils.Test(
        'test_multi_tenant_managed_jobs',
        [
            'echo "==== Test multi-tenant managed jobs ===="',
            *set_user(user_1, user_1_name, [
                f'sky jobs launch -n {name}-1 --cloud {generic_cloud} --cpus 2+ tests/test_yamls/minimal.yaml -y',
                f's=$(sky jobs queue) && echo "$s" && echo "$s" | grep {name}-1 | grep SUCCEEDED',
                f's=$(sky jobs queue -u) && echo "$s" && echo "$s" | grep {user_1_name} | grep {name}-1 | grep SUCCEEDED',
            ]),
            *set_user(user_2, user_2_name, [
                f's=$(sky jobs queue) && echo "$s" && echo "$s" | grep {name}-1 && exit 1 || true',
                f's=$(sky jobs queue -u) && echo "$s" && echo "$s" | grep {user_1_name} | grep {name}-1 | grep SUCCEEDED',
                f'sky jobs launch -n {name}-2 --cloud {generic_cloud} --cpus 2+ tests/test_yamls/minimal.yaml -y',
                f's=$(sky jobs queue) && echo "$s" && echo "$s" | grep {name}-2 | grep SUCCEEDED',
                f's=$(sky jobs queue -u) && echo "$s" && echo "$s" | grep {user_2_name} | grep {name}-2 | grep SUCCEEDED',
            ]),
            'echo "==== Test jobs controller cluster user ===="',
            f's=$(sky status -u) && echo "$s" && echo "$s" | grep sky-jobs-controller- | grep -v {user_1_name} | grep -v {user_2_name}',
            'echo "==== Test controller down blocked by other users ===="',
            *set_user(user_1, user_1_name, [
                f'sky jobs launch -n {name}-3 --cloud {generic_cloud} --cpus 2+ -y -d sleep 1000',
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=f'{name}-3',
                    job_status=[
                        sky.ManagedJobStatus.PENDING,
                        sky.ManagedJobStatus.SUBMITTED,
                        sky.ManagedJobStatus.STARTING,
                        sky.ManagedJobStatus.RUNNING
                    ],
                    timeout=60),
            ]),
            *set_user(user_2, user_2_name, [
                f'controller=$(sky status -u | grep sky-jobs-controller- | awk \'{{print $1}}\') && echo "$controller" && echo delete | sky down "$controller" && exit 1 || true',
                f'sky jobs cancel -y -n {name}-3',
            ]),
        ],
        f'sky jobs cancel -y -n {name}-1; sky jobs cancel -y -n {name}-2; sky jobs cancel -y -n {name}-3',
    )
    smoke_tests_utils.run_one_test(test)
