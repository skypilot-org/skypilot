from typing import List

from smoke_tests import smoke_tests_utils

from sky.skylet import constants


# ---------- Test multi-tenant ----------
def test_multi_tenant(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef12'
    user_1_name = 'user1'
    user_2 = 'abcdef13'
    user_2_name = 'user2'

    def set_user(user_id: str, user_name: str,
                 commands: List[str]) -> List[str]:
        return [
            f'export {constants.USER_ID_ENV_VAR}="{user_id}"; '
            f'export {constants.USER_ENV_VAR}="{user_name}"; ' + cmd
            for cmd in commands
        ]

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
                f'sky launch -y -c {name}-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -n job-1 tests/test_yamls/minimal.yaml',
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
                    f'sky launch -y -c {name}-2 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -n job-3 tests/test_yamls/minimal.yaml',
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
