# Smoke tests for SkyPilot workspaces functionality.

import datetime
import json
import os
import tempfile
import textwrap
import time

import boto3
import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky import skypilot_config


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
