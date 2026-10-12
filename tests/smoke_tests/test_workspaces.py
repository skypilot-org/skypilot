# Smoke tests for SkyPilot workspaces functionality.

import datetime
import hashlib
import json
import os
import tempfile
import textwrap
import time
import uuid

import boto3
import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky import skypilot_config
from sky.skylet import constants
from sky.utils import common_utils


# ---------- Test workspace switching ----------
@pytest.mark.no_remote_server
@pytest.mark.no_dependency
def test_workspace_switching(generic_cloud: str):
    # Test switching between workspaces by modifying .sky.yaml.
    #
    # This test reproduces a scenario where:
    # 1. User creates an empty .sky.yaml file
    # 2. Launches a cluster with workspace "ws-default"
    # 3. Updates .sky.yaml to set "ws-train" as active workspace
    # 4. Launches another cluster with workspace "train-ws"
    # 5. Verifies both workspaces function correctly
    if not smoke_tests_utils.is_in_buildkite_env():
        pytest.skip(
            'Skipping workspace switching test when not in Buildkite environment'
        )
    if smoke_tests_utils.is_remote_server_test():
        pytest.skip(
            'This test requires a local API server and needs to restart the server during execution. '
            'If the API server endpoint is set in the environment file, restarting is not supported, '
            'so the test will be skipped.')

    ws1_name = 'ws-1'
    ws2_name = 'ws-2'
    server_config_content = textwrap.dedent(f"""\
        workspaces:
            {ws1_name}: {{}}
            {ws2_name}: {{}}
    """)
    ws1_config_content = textwrap.dedent(f"""\
        active_workspace: {ws1_name}
    """)
    ws2_config_content = textwrap.dedent(f"""\
        active_workspace: {ws2_name}
    """)
    with tempfile.NamedTemporaryFile(prefix='server_config_',
                                     delete=False,
                                     mode='w') as f:
        f.write(server_config_content)
        server_config_path = f.name

    with tempfile.NamedTemporaryFile(prefix='ws1_', delete=False,
                                     mode='w') as f:
        f.write(ws1_config_content)
        ws1_config_path = f.name

    with tempfile.NamedTemporaryFile(prefix='ws2_', delete=False,
                                     mode='w') as f:
        f.write(ws2_config_content)
        ws2_config_path = f.name

    change_config_cmd = 'rm -f .sky.yaml || true && cp {config_path} .sky.yaml'

    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test_workspace_switching',
        [
            f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}={server_config_path} && {smoke_tests_utils.SKY_API_RESTART}',
            # Launch first cluster with workspace ws-default
            change_config_cmd.format(config_path=ws1_config_path),
            f'sky launch -y --async -c {name}-1 '
            f'--infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'echo hi',
            # Launch second cluster with workspace train-ws
            change_config_cmd.format(config_path=ws2_config_path),
            f'sky launch -y -c {name}-2 '
            f'--infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'echo hi',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                f'{name}-1', [sky.ClusterStatus.UP],
                timeout=smoke_tests_utils.get_timeout(generic_cloud)),
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 | grep {ws1_name}',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep {ws2_name}',
            change_config_cmd.format(config_path=ws1_config_path),
            f's=$(sky down -y {name}-1 {name}-2); echo "$s"; echo "$s" | grep "is in workspace {ws2_name!r}, but the active workspace is {ws1_name!r}"',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep UP',
            f's=$(sky down -y {name}-2 2>&1); echo "$s"; echo "$s" | grep "is in workspace {ws2_name!r}, but the active workspace is {ws1_name!r}"',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep UP',
            f'rm -f .sky.yaml || true',
            f's=$(sky down -y {name}-2 2>&1); echo "$s"; echo "$s" | grep "is in workspace {ws2_name!r}, but the active workspace is \'default\'"',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep UP',
            change_config_cmd.format(config_path=ws2_config_path),
            f's=$(sky down -y {name}-2 2>&1); echo "$s"; echo "$s" | grep "Terminating cluster {name}-2...done."',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 && exit 1 || true',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 && exit 1 || true',
        ],
        teardown=(
            f'{change_config_cmd.format(config_path=ws1_config_path)} && sky down -y {name}-1; '
            f'{change_config_cmd.format(config_path=ws2_config_path)} && sky down -y {name}-2; '
            f'rm -f .sky.yaml || true; '
            # restore the original config
            f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}= && {smoke_tests_utils.SKY_API_RESTART}'
        ),
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)
    os.unlink(server_config_path)
    os.unlink(ws1_config_path)
    os.unlink(ws2_config_path)


# ---------- Test private-workspace access for a pre-login user ----------
@pytest.mark.no_remote_server
@pytest.mark.no_dependency
def test_workspace_private_access_pre_login_user(generic_cloud: str):
    """A user added to allowed_users before first login gets access on login.

    Reproduces the bug where an admin adds a user to a private workspace's
    ``allowed_users`` before that user has ever contacted the server. The user
    record only exists after first authenticated contact, so the startup
    config->policy sync drops the entry. Without the fix, the user still has no
    workspace access after their first login and hits NoWorkspaceAccessError;
    the fix re-resolves the config on user creation and grants the missing
    policy.

    Requires a local API server so we can restart it with a private-workspace
    config. The user's first authenticated contact is simulated with an
    ``X-Auth-Request-Email`` header (the auth-proxy identity path), which is
    what lazily creates the user record and runs the new-user policy setup.
    ``rbac.default_role: user`` makes the new user a non-admin, so their
    workspace access depends entirely on the private workspace's
    ``allowed_users`` (admins can access every workspace and would mask the
    bug).

    After the access assertion, launches a tiny cluster as that user (the
    private workspace is their only accessible one, so the server-side resolver
    auto-selects it) and asserts via ``sky status`` that the cluster landed in
    the private workspace.
    """
    if smoke_tests_utils.is_remote_server_test():
        pytest.skip(
            'This test requires a local API server it can restart with a '
            'private-workspace config; skipped against a remote endpoint.')

    # Unique per run so no user record exists at server-startup sync time and
    # runs do not contaminate each other via the persisted users table.
    suffix = uuid.uuid4().hex[:8]
    user_name = f'presync-{suffix}@example.com'
    ws_name = f'private-ws-{suffix}'
    base_url = smoke_tests_utils.ENDPOINT.rsplit('/api/health', 1)[0]
    name = smoke_tests_utils.get_cluster_name()
    # The auth-proxy middleware derives the user id from the identity header
    # as md5(user_name)[:USER_HASH_LENGTH] (see
    # sky/server/server.py::_extract_user_from_header). The CLI ships the
    # SKYPILOT_USER_ID / SKYPILOT_USER env vars as the request identity
    # (common_utils.get_user_hash / get_local_user_name), so exporting the
    # same hash+name makes `sky launch` / `sky status` run as the user the
    # curl step logged in.
    user_hash = hashlib.md5(
        user_name.encode(),
        usedforsecurity=False).hexdigest()[:common_utils.USER_HASH_LENGTH]
    as_user = (f'export {constants.USER_ID_ENV_VAR}="{user_hash}"; '
               f'export {constants.USER_ENV_VAR}="{user_name}"; ')

    server_config_content = textwrap.dedent(f"""\
        rbac:
          default_role: user
        workspaces:
          default:
            private: true
          {ws_name}:
            private: true
            allowed_users:
              - {user_name}
    """)
    with tempfile.NamedTemporaryFile(prefix='server_config_',
                                     delete=False,
                                     mode='w') as f:
        f.write(server_config_content)
        server_config_path = f.name

    # A single authenticated GET of /users/me/workspace as the pre-login user:
    #   * the auth middleware creates the user record and runs the new-user
    #     policy setup (the fix grants the private-workspace policy here);
    #   * the handler then reports the user's accessible workspaces.
    # With the fix, `accessible` includes ws_name. Without it, `accessible` is
    # empty (NoWorkspaceAccessError) and the assertion below fails with a repro
    # message.
    assert_access_cmd = (
        f'resp=$(curl -sS -H "X-Auth-Request-Email: {user_name}" '
        f'{base_url}/users/me/workspace); echo "$resp"; '
        f'echo "$resp" | python3 -c "import sys, json; '
        f'd = json.load(sys.stdin); '
        f'''assert {ws_name!r} in d.get('accessible', []), '''
        f'''\\"REPRO: pre-login user denied private-workspace access: \\" '''
        f'+ repr(d)"')

    test = smoke_tests_utils.Test(
        'test_workspace_private_access_pre_login_user',
        [
            f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}='
            f'{server_config_path} && {smoke_tests_utils.SKY_API_RESTART}',
            assert_access_cmd,
            # Enable the cloud on the freshly-restarted server. Runs as the
            # test user, whose requests resolve to the private workspace.
            f'{as_user}sky check {generic_cloud}',
            # Launch as the user. The private workspace is their only
            # accessible workspace, so the server-side resolver must
            # auto-select it (single-membership).
            f'{as_user}sky launch -y -c {name} --infra {generic_cloud} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} echo hi',
            # The cluster row must show the private workspace.
            f'{as_user}s=$(sky status); echo "$s"; '
            f'echo "$s" | grep {name} | grep {ws_name}',
        ],
        teardown=(
            # Same identity + same server config, so the resolver picks the
            # cluster's workspace and the down is not rejected for a
            # workspace mismatch.
            f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}='
            f'{server_config_path}; {as_user}sky down -y {name} || true; '
            f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}= && '
            f'{smoke_tests_utils.SKY_API_RESTART}'),
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    # Skip the default `sky status -u` preamble: it authenticates as the ambient
    # user, not the pre-login user we exercise via the auth header.
    smoke_tests_utils.run_one_test(test, check_sky_status=False)
    os.unlink(server_config_path)


def _verify_cluster_created_by_user(cluster_name: str,
                                    region: str,
                                    expected_user_name: str,
                                    should_match: bool = True):
    """Verify that a cluster was created by a specific AWS user via CloudTrail.

    Polls CloudTrail every 15 seconds until the RunInstances event is found.

    Args:
        cluster_name: Full cluster name (e.g., 'my-cluster-1')
        region: AWS region
        expected_user_name: Expected IAM user name (or substring to match)
        should_match: If True, verify user_name is in identity. If False, verify it's NOT.

    Returns:
        str: The identity (userName or ARN) that created the cluster
    """
    cloudtrail = boto3.client('cloudtrail', region_name=region)
    ec2 = boto3.client('ec2', region_name=region)

    # Get instance IDs for the cluster
    response = ec2.describe_instances(Filters=[{
        'Name': 'tag:skypilot-cluster-name',
        'Values': [f'{cluster_name}*']
    }])
    instance_ids = []
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            instance_ids.append(instance['InstanceId'])

    if not instance_ids:
        raise ValueError(f'No instances found for cluster {cluster_name}')

    start_time = datetime.datetime.now(
        datetime.timezone.utc) - datetime.timedelta(hours=1)
    attempt = 0

    while True:
        if attempt > 0:
            print(
                f'CloudTrail check attempt {attempt + 1} for {cluster_name} (waiting for events to appear...)'
            )

        for instance_id in instance_ids:
            try:
                response = cloudtrail.lookup_events(LookupAttributes=[{
                    'AttributeKey': 'ResourceName',
                    'AttributeValue': instance_id
                }],
                                                    StartTime=start_time,
                                                    MaxResults=500)
                for event in response.get('Events', []):
                    if event['EventName'] == 'RunInstances':
                        event_data = json.loads(event['CloudTrailEvent'])
                        user_identity = event_data.get('userIdentity', {})
                        identity = user_identity.get(
                            'userName') or user_identity.get('arn', '')
                        if identity:
                            print(
                                f'{cluster_name} instance {instance_id} created by: {identity}'
                            )
                            if should_match:
                                if expected_user_name not in identity:
                                    raise ValueError(
                                        f'{cluster_name} should be created by user {expected_user_name}, '
                                        f'but was: {identity}')
                            else:
                                if expected_user_name in identity:
                                    raise ValueError(
                                        f'{cluster_name} should NOT be created by user {expected_user_name}, '
                                        f'but was: {identity}')
                            event_str = json.dumps(event, indent=2, default=str)
                            return f'Correctly verified that {cluster_name} was created by {identity}:\n{event_str}'
            except Exception as e:
                # Continue polling if event not found yet
                if 'No events found' not in str(
                        e) and 'does not exist' not in str(e).lower():
                    print(
                        f'Warning: Error checking instance {instance_id}: {e}')

        attempt += 1
        time.sleep(15)


@pytest.mark.no_remote_server
@pytest.mark.aws
def test_workspace_multiple_aws_profiles():
    """Test AWS with multiple workspaces and AWS profiles."""
    # Extract default credentials.
    default_access_key, default_secret_key = smoke_tests_utils.extract_default_aws_credentials(
    )
    if not default_access_key or not default_secret_key:
        pytest.fail('Default AWS credentials not found')

    # Create temporary credentials file
    temp_credentials_file = tempfile.NamedTemporaryFile(
        prefix='aws_credentials_', mode='w', delete=False)
    temp_credentials_path = temp_credentials_file.name
    temp_credentials_file.close()

    ws1_name = 'team-a'
    ws2_name = 'team-b'
    test_profile_name = 'team-b-profile'

    # Configure workspaces with different AWS profiles
    server_config_content = textwrap.dedent(f"""\
        workspaces:
            {ws1_name}:
                aws:
                    profile: default
            {ws2_name}:
                aws:
                    profile: {test_profile_name}
    """)
    with tempfile.NamedTemporaryFile(prefix='server_config_aws_profile_',
                                     delete=False,
                                     mode='w') as f:
        f.write(server_config_content)
        server_config_path = f.name

    region = 'us-east-2'
    name = smoke_tests_utils.get_cluster_name()
    iam_user_name = f'test-user-{name}'

    test = smoke_tests_utils.Test(
        'test_aws_workspace_profile',
        [
            f'aws iam create-user --user-name {iam_user_name} --output json',
            f'aws iam attach-user-policy --user-name {iam_user_name} --policy-arn arn:aws:iam::aws:policy/AmazonEC2FullAccess',
            f'aws iam attach-user-policy --user-name {iam_user_name} --policy-arn arn:aws:iam::aws:policy/IAMFullAccess',

            # Write temp credentials file.
            f'access_key_output=$(aws iam create-access-key --user-name {iam_user_name} --output json); '
            f'access_key_id=$(echo "$access_key_output" | jq -r ".AccessKey.AccessKeyId"); '
            f'secret_key=$(echo "$access_key_output" | jq -r ".AccessKey.SecretAccessKey"); '
            f'echo "[default]" > {temp_credentials_path}; '
            f'echo "aws_access_key_id = {default_access_key}" >> {temp_credentials_path}; '
            f'echo "aws_secret_access_key = {default_secret_key}" >> {temp_credentials_path}; '
            f'echo "" >> {temp_credentials_path}; '
            f'echo "[{test_profile_name}]" >> {temp_credentials_path}; '
            f'echo "aws_access_key_id = $access_key_id" >> {temp_credentials_path}; '
            f'echo "aws_secret_access_key = $secret_key" >> {temp_credentials_path}; '
            f'echo "Created credentials file at {temp_credentials_path}"',
            # Restart API server with updated config and credentials.
            f'export AWS_SHARED_CREDENTIALS_FILE={temp_credentials_path} && '
            f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}={server_config_path} && '
            f'{smoke_tests_utils.SKY_API_RESTART}',

            # Launch cluster 1 with team-a workspace
            f'sky launch -y -c {name}-1 --infra aws/{region} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'--config active_workspace={ws1_name} echo hi',
            # Launch cluster 2 with team-b workspace
            f'sky launch -y -c {name}-2 --infra aws/{region} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'--config active_workspace={ws2_name} echo hi',

            # Verify clusters are in correct workspaces
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-1 | grep {ws1_name}',
            f's=$(sky status); echo "$s"; echo "$s" | grep {name}-2 | grep {ws2_name}',

            # Query CloudTrail for RunInstances events and verify userIdentity
            # Poll every 15 seconds until CloudTrail events appear (can take 5-15 minutes)
            # Fetch instance IDs first, then query CloudTrail by ResourceName (instance ID)
            # Then verify the userIdentity is correct, to make sure we are using the correct AWS profile.
            (lambda: _verify_cluster_created_by_user(
                f'{name}-1', region, iam_user_name, should_match=False)),
            (lambda: _verify_cluster_created_by_user(
                f'{name}-2', region, iam_user_name, should_match=True)),

            # Test autostop with workspaces
            f'sky autostop -y {name}-1 --down -i 1',
            f'sky autostop -y {name}-2 --down -i 1',
            # Verify autostop is set for both clusters
            f's=$(sky status); echo "$s"; echo "==check autostop set=="; echo "$s" | grep {name}-1 | grep "1m (down)"',
            f's=$(sky status); echo "$s"; echo "==check autostop set=="; echo "$s" | grep {name}-2 | grep "1m (down)"',
            # Ensure the clusters are not terminated early
            'sleep 40',
            f's=$(sky status {name}-1 --refresh); echo "$s"; echo "$s" | grep {name}-1 | grep UP',
            f's=$(sky status {name}-2 --refresh); echo "$s"; echo "$s" | grep {name}-2 | grep UP',
            # Wait for autodown
            smoke_tests_utils.get_cmd_wait_until_cluster_is_not_found(
                f'{name}-1', timeout=300),
            smoke_tests_utils.get_cmd_wait_until_cluster_is_not_found(
                f'{name}-2', timeout=300),
        ],
        teardown=
        (f'sky down -y {name}-1 --config active_workspace={ws1_name} || true; '
         f'sky down -y {name}-2 --config active_workspace={ws2_name} || true; '
         f'for key_id in $(aws iam list-access-keys --user-name {iam_user_name} '
         f'--query "AccessKeyMetadata[].AccessKeyId" --output text); do '
         f'aws iam delete-access-key --user-name {iam_user_name} --access-key-id $key_id; '
         f'done; '
         f'aws iam detach-user-policy --user-name {iam_user_name} --policy-arn arn:aws:iam::aws:policy/AmazonEC2FullAccess || true; '
         f'aws iam detach-user-policy --user-name {iam_user_name} --policy-arn arn:aws:iam::aws:policy/IAMFullAccess || true; '
         f'aws iam delete-user --user-name {iam_user_name} || true; '
         f'rm -f {temp_credentials_path} || true; '
         f'unset AWS_SHARED_CREDENTIALS_FILE || true; '
         f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}= && {smoke_tests_utils.SKY_API_RESTART}'
        ),
        timeout=30 * 60,
    )

    try:
        smoke_tests_utils.run_one_test(test)
    finally:
        # Cleanup temp files
        os.unlink(server_config_path)
        if os.path.exists(temp_credentials_path):
            os.unlink(temp_credentials_path)


# ---------- Test per-workspace Kubernetes remote_identity ----------
@pytest.mark.kubernetes
@pytest.mark.no_remote_server
# We can't restart the api server in the dependency test.
@pytest.mark.no_dependency
@pytest.mark.no_remote_identity_none  # Sets other identities.
def test_workspace_k8s_remote_identity():
    """Does each team's cluster run under its own Kubernetes ServiceAccount?

    One server, two workspaces:
      team-a: no override            -> default identity
      team-b: remote_identity: <sa>  -> its own identity

    Before per-workspace remote_identity, everyone got the global identity.
    This proves team-b's override is applied at provisioning time and does NOT
    leak into team-a. We don't trust SkyPilot's logs -- we ask Kubernetes
    directly (pod spec.serviceAccountName), the same way
    test_workspace_multiple_aws_profiles asks CloudTrail who launched an
    instance.
    """
    if not smoke_tests_utils.is_in_buildkite_env():
        pytest.skip(
            'Skipping workspace remote_identity test when not in Buildkite '
            'environment')

    ws1_name = 'team-a'  # No override -> default identity.
    ws2_name = 'team-b'  # Its own service account.
    sa_name = 'team-b-sa'

    # One server config that defines two workspaces -- exactly what a platform
    # admin would write for multi-tenancy.
    server_config_content = textwrap.dedent(f"""\
        workspaces:
            {ws1_name}:
                kubernetes: {{}}
            {ws2_name}:
                kubernetes:
                    remote_identity: {sa_name}
    """)
    with tempfile.NamedTemporaryFile(prefix='server_config_k8s_remote_id_',
                                     delete=False,
                                     mode='w') as f:
        f.write(server_config_content)
        server_config_path = f.name

    name = smoke_tests_utils.get_cluster_name()
    max_len = sky.Kubernetes.max_cluster_name_length()
    name_on_cloud_1 = common_utils.make_cluster_name_on_cloud(
        f'{name}-1', max_len)
    name_on_cloud_2 = common_utils.make_cluster_name_on_cloud(
        f'{name}-2', max_len)

    def _get_pod_sa_cmd(name_on_cloud: str) -> str:
        return (f'kubectl get pod -l skypilot-cluster-name={name_on_cloud} '
                "-o jsonpath='{.items[0].spec.serviceAccountName}'")

    test = smoke_tests_utils.Test(
        'test_workspace_k8s_remote_identity',
        [
            # Pre-create the ServiceAccount team-b expects -- SkyPilot does not
            # create custom SAs; the admin owns them.
            f'kubectl create serviceaccount {sa_name} || true',
            # Restart the API server with this two-workspace config loaded.
            f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}={server_config_path} && '
            f'{smoke_tests_utils.SKY_API_RESTART}',
            # Launch one cluster as each team.
            f'sky launch -y -c {name}-1 --infra kubernetes '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'--config active_workspace={ws1_name} echo hi',
            f'sky launch -y -c {name}-2 --infra kubernetes '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'--config active_workspace={ws2_name} echo hi',
            # The custom identity did not break normal operation.
            f'sky logs {name}-1 1 --status',
            f'sky logs {name}-2 1 --status',
            # Ask Kubernetes who the pods REALLY run as.
            # team-b's override MUST be applied at provisioning time.
            f'sa=$({_get_pod_sa_cmd(name_on_cloud_2)}); '
            f'echo "team-b pod serviceAccountName: $sa"; '
            f'[ "$sa" = "{sa_name}" ]',
            # Control group: team-b's identity MUST NOT leak into team-a, and a
            # no-override workspace still gets the default. (Isolation!)
            f'sa=$({_get_pod_sa_cmd(name_on_cloud_1)}); '
            f'echo "team-a pod serviceAccountName: $sa"; '
            f'[ "$sa" != "{sa_name}" ]',
        ],
        teardown=
        (f'sky down -y {name}-1 --config active_workspace={ws1_name} || true; '
         f'sky down -y {name}-2 --config active_workspace={ws2_name} || true; '
         f'kubectl delete serviceaccount {sa_name} || true; '
         f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}= && '
         f'{smoke_tests_utils.SKY_API_RESTART}'),
        timeout=20 * 60,
    )

    try:
        smoke_tests_utils.run_one_test(test)
    finally:
        os.unlink(server_config_path)


# ---------- Test per-workspace prefix in a shared jobs bucket ----------
@pytest.mark.aws
@pytest.mark.managed_jobs
@pytest.mark.no_remote_server
# We can't restart the api server in the dependency test.
@pytest.mark.no_dependency
def test_workspace_jobs_bucket_prefix(generic_cloud: str):
    """Do workspaces sharing `jobs.bucket` upload under separate prefixes?

    One server, one shared bucket, five workspaces:
      team-a   -> s3://<bucket>/<root>/workspaces/team-a/job-<run_id>/...
      team-b   -> s3://<bucket>/<root>/workspaces/team-b/job-<run_id>/...
      Research -> s3://<bucket>/<root>/workspaces/research.979d6300/...
      ml/prod  -> s3://<bucket>/<root>/workspaces/ml-prod.c0f7bb6f/...
      default  -> s3://<bucket>/<root>/workspaces/default/job-<run_id>/...
    `Research` and `ml/prod` stand for workspaces created before names were
    validated; they map to a slug plus a hash of the exact name.

    Bucket IAM can only enforce per-workspace RBAC if every object a job
    uploads (workdir, folder mounts, single-file mounts) sits under its
    workspace prefix. We list the bucket directly instead of trusting
    SkyPilot's logs. Every job runs to completion to show its uploads are
    usable from its workspace. Afterwards every job sub-path must be cleaned
    up while the user-owned bucket survives.
    """
    ws_default = constants.SKYPILOT_DEFAULT_WORKSPACE
    # (job name suffix, workspace, expected bucket key segment)
    team_workspaces = [
        ('a', 'team-a', 'team-a'),
        ('b', 'team-b', 'team-b'),
        ('r', 'Research', 'research.979d6300'),
        ('m', 'ml/prod', 'ml-prod.c0f7bb6f'),
    ]
    all_workspaces = team_workspaces + [('d', ws_default, ws_default)]
    name = smoke_tests_utils.get_cluster_name()
    bucket_name = f'sky-ws-bucket-{int(time.time())}-{uuid.uuid4().hex[:6]}'
    # A sub-path on jobs.bucket covers joining it with the workspace prefix.
    bucket_root = 'smoke-root'

    # Overlaid onto the existing server config by override_sky_config, so
    # settings the test env relies on are kept. Config.update is shallow, so
    # `jobs` must carry the low controller resources alongside the bucket.
    low_resource = smoke_tests_utils.LOW_CONTROLLER_RESOURCE_OVERRIDE_CONFIG
    config_dict = {
        **low_resource,
        'jobs': {
            **low_resource['jobs'],
            'bucket': f's3://{bucket_name}/{bucket_root}',
        },
        'workspaces': {ws: {} for _, ws, _ in team_workspaces},
    }

    # Every kind of local upload: workdir, a folder mount, a single file.
    local_dir_obj = tempfile.TemporaryDirectory(prefix='sky_ws_bucket_')
    local_dir = local_dir_obj.name
    workdir = os.path.join(local_dir, 'workdir')
    folder = os.path.join(local_dir, 'folder')
    os.makedirs(workdir)
    os.makedirs(folder)
    with open(os.path.join(workdir, 'marker.txt'), 'w') as f:
        f.write('workdir\n')
    with open(os.path.join(folder, 'data.txt'), 'w') as f:
        f.write('folder\n')
    single_file = os.path.join(local_dir, 'one.txt')
    with open(single_file, 'w') as f:
        f.write('file\n')
    task_yaml = os.path.join(local_dir, 'task.yaml')
    with open(task_yaml, 'w') as f:
        f.write(
            textwrap.dedent(f"""\
            workdir: {workdir}
            file_mounts:
              /folder: {folder}
              /one.txt: {single_file}
            run: |
              set -ex
              cat marker.txt
              cat /folder/data.txt
              cat /one.txt
            """))

    list_keys = f'aws s3 ls --recursive s3://{bucket_name}/'
    # Each launch's output, and the bucket sub-path its job uploaded to.
    run_dir = os.path.join(local_dir, 'runs')
    os.makedirs(run_dir)

    def _launch_cmd(job_name: str, ws: str) -> str:
        return (f'set -o pipefail; sky jobs launch -y -d -n {job_name} '
                f'--infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} '
                f'--config active_workspace={ws} {task_yaml} 2>&1 | '
                f'tee {run_dir}/{job_name}.log')

    def _check_job_upload_cmd(job_name: str, segment: str) -> str:
        # Find the sub-path of the upload that belongs to this job: the last
        # one synced before `Managed Job ID` is printed. If the server retries
        # the launch request, earlier attempts sync to other sub-paths that no
        # job refers to; those are not this job's upload.
        return (
            'out=$(sed "s/\\x1b\\[[0-9;?]*[A-Za-z]//g" '
            f'{run_dir}/{job_name}.log | '
            'awk "{print} /Managed Job ID:/{exit}"); '
            'job_id=$(echo "$out" | grep -oE "Managed Job ID: [0-9]+" | '
            'grep -oE "[0-9]+$"); '
            'run=$(echo "$out" | grep "Storage synced" | '
            f'grep -oE "{bucket_root}/workspaces/{_ere(segment)}/'
            'job-[a-z0-9]+" | '
            'tail -n 1); '
            f'echo "{job_name}: job $job_id uploaded to $run"; '
            '[ -n "$job_id" ] && [ -n "$run" ] || exit 1; '
            f'echo "$run" > {run_dir}/{job_name}.run; '
            f'keys=$(aws s3 ls --recursive s3://{bucket_name}/$run/); '
            'echo "$keys"; '
            'echo "$keys" | grep -E " $run/workdir/marker.txt$" && '
            'echo "$keys" | grep -E " $run/local-file-mounts/[0-9]+/data.txt$" '
            '&& echo "$keys" | grep -E " $run/tmp-files/file-0$"')

    def _ere(text: str) -> str:
        # The only regex metacharacter a bucket key segment can contain.
        return text.replace('.', '\\.')

    def _cancel_cmd(job_name: str, ws: str) -> str:
        # Cancel is workspace-scoped.
        return (f'sky jobs cancel -y -n {job_name} '
                f'--config active_workspace={ws}')

    wait_for_job = (
        smoke_tests_utils.
        get_cmd_wait_until_managed_job_status_contains_matching_job_name)

    test = smoke_tests_utils.Test(
        'test_workspace_jobs_bucket_prefix',
        [
            # The admin owns the shared bucket; SkyPilot must not delete it.
            f'aws s3api create-bucket --bucket {bucket_name}',
            # Restart so the server picks up the merged config (existing
            # server config + team workspaces + shared bucket).
            f'{smoke_tests_utils.SKY_API_RESTART}',
            # Uploads finish before `jobs launch -d` returns, and the job
            # sub-path is only deleted after the job ends, so the objects are
            # listable right after each launch.
            *[
                cmd for suffix, ws, segment in all_workspaces
                for cmd in (_launch_cmd(f'{name}-{suffix}', ws),
                            _check_job_upload_cmd(f'{name}-{suffix}', segment))
            ],
            # Nothing is written outside a workspace prefix.
            f'keys=$({list_keys}); echo "$keys"; '
            f'! echo "$keys" | grep -vE " {bucket_root}/workspaces/'
            f'({"|".join(_ere(seg) for _, _, seg in all_workspaces)})/job-"',
            # The uploads were usable by every job, in every workspace.
            *[
                wait_for_job(job_name=f'{name}-{suffix}',
                             job_status=[sky.ManagedJobStatus.SUCCEEDED],
                             timeout=900) for suffix, _, _ in all_workspaces
            ],
            # Cleanup finds each job's sub-path under its workspace prefix.
            'for i in $(seq 1 36); do '
            '  left=""; '
            f'  for run in $(cat {run_dir}/*.run); do '
            f'    aws s3 ls --recursive s3://{bucket_name}/$run/ | grep -q . '
            '&& left="$left $run"; '
            '  done; '
            '  [ -z "$left" ] && exit 0; '
            '  sleep 5; '
            'done; echo "job sub-paths not cleaned up:$left"; exit 1',
            # The user-owned bucket itself survives.
            f'aws s3api head-bucket --bucket {bucket_name}',
        ],
        teardown=(''.join(f'{_cancel_cmd(f"{name}-{suffix}", ws)} || true; '
                          for suffix, ws, _ in all_workspaces) +
                  f'aws s3 rb s3://{bucket_name} --force || true; '
                  f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}= && '
                  f'{smoke_tests_utils.SKY_API_RESTART}'),
        config_dict=config_dict,
        timeout=40 * 60,
    )

    try:
        smoke_tests_utils.run_one_test(test)
    finally:
        local_dir_obj.cleanup()


# ---------- Test managed jobs in a non-default workspace ----------
@pytest.mark.managed_jobs
@pytest.mark.no_remote_server
# We can't restart the api server in the dependency test.
@pytest.mark.no_dependency
def test_managed_jobs_in_non_default_workspace(generic_cloud: str):
    """Does a managed job in a non-default workspace run to completion?

    The jobs controller launches the job's cluster through an API server of
    its own, which has none of the main API server's config (no `workspaces`)
    and has only just seen the job's user. The workspace check there must
    still let the job launch, or the job retries on PermissionDeniedError and
    stays PENDING forever.
    """
    workspace = 'team-a'
    name = smoke_tests_utils.get_cluster_name()
    config_dict = {
        **smoke_tests_utils.LOW_CONTROLLER_RESOURCE_OVERRIDE_CONFIG,
        'workspaces': {
            workspace: {},
        },
    }
    wait_succeeded = (
        smoke_tests_utils.
        get_cmd_wait_until_managed_job_status_contains_matching_job_name(
            job_name=name,
            job_status=[sky.ManagedJobStatus.SUCCEEDED],
            timeout=900))

    test = smoke_tests_utils.Test(
        'test_managed_jobs_in_non_default_workspace',
        [
            # Restart so the server picks up the merged config (existing
            # server config + the workspace).
            smoke_tests_utils.SKY_API_RESTART,
            f'sky jobs launch -y -d -n {name} --infra {generic_cloud} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'--config active_workspace={workspace} echo hi',
            # On failure, show why the controller could not launch the job.
            # The waiter runs in a subshell: it ends with `exit 1` on timeout.
            f'( {wait_succeeded} ) || {{ sky jobs logs --controller '
            f'-n {name} --no-follow --config active_workspace={workspace} | '
            'tail -n 40; exit 1; }',
        ],
        teardown=(f'sky jobs cancel -y -n {name} '
                  f'--config active_workspace={workspace} || true; '
                  f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}= && '
                  f'{smoke_tests_utils.SKY_API_RESTART}'),
        config_dict=config_dict,
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)
