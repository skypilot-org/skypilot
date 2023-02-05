import os
import pytest
import tempfile
import textwrap
from typing import List

# Usage: use
#   @pytest.mark.slow
# to mark a test as slow and to skip by default.
# https://docs.pytest.org/en/latest/example/simple.html#control-skipping-of-tests-according-to-command-line-option

# By default, only run generic tests and cloud-specific tests for GCP and Azure,
# due to the cloud credit limit for the development account.
# To only run tests for a specific cloud (as well as generic tests), use
# --aws, --gcp, --azure, or --lambda.
# To only run tests for managed spot (without generic tests), use --managed-spot.
# A "generic test" tests a generic functionality (e.g., autostop) that
# should work on any cloud we support. The cloud used for such a test
# is controlled by `--generic-cloud` (typically you do not need to set it).
all_clouds_in_smoke_tests = ['aws', 'gcp', 'azure', 'lambda']
default_clouds_to_run = ['gcp', 'azure']

# Translate cloud name to pytest keyword. We need this because
# @pytest.mark.lambda is not allowed, so we use @pytest.mark.lambda_cloud
# instead.
cloud_to_pytest_keyword = {
    'aws': 'aws',
    'gcp': 'gcp',
    'azure': 'azure',
    'lambda': 'lambda_cloud'
}


def pytest_addoption(parser):
    # tests marked as `slow` will be skipped by default, use --runslow to run
    parser.addoption('--runslow',
                     action='store_true',
                     default=False,
                     help='run slow tests.')
    for cloud in all_clouds_in_smoke_tests:
        parser.addoption(f'--{cloud}',
                         action='store_true',
                         default=False,
                         help=f'Only run {cloud.upper()} tests.')
    parser.addoption('--managed-spot',
                     action='store_true',
                     default=False,
                     help='Only run tests for managed spot.')
    parser.addoption(
        '--generic-cloud',
        type=str,
        default='gcp',
        choices=all_clouds_in_smoke_tests,
        help='Cloud to use for generic tests. If the generic cloud is '
        'not within the clouds to be run, it will be reset to the first '
        'cloud in the list of the clouds to be run.')


def pytest_configure(config):
    config.addinivalue_line('markers', 'slow: mark test as slow to run')
    for cloud in all_clouds_in_smoke_tests:
        cloud_keyword = cloud_to_pytest_keyword[cloud]
        config.addinivalue_line(
            'markers', f'{cloud_keyword}: mark test as {cloud} specific')


def _get_cloud_to_run(config) -> List[str]:
    cloud_to_run = []
    for cloud in all_clouds_in_smoke_tests:
        if config.getoption(f'--{cloud}'):
            cloud_to_run.append(cloud)
    if not cloud_to_run:
        cloud_to_run = default_clouds_to_run
    return cloud_to_run


def pytest_collection_modifyitems(config, items):
    skip_marks = {}
    skip_marks['slow'] = pytest.mark.skip(reason='need --runslow option to run')
    skip_marks['managed_spot'] = pytest.mark.skip(
        reason='skipped, because --managed-spot option is set')
    for cloud in all_clouds_in_smoke_tests:
        skip_marks[cloud] = pytest.mark.skip(
            reason=f'tests for {cloud} is skipped, try setting --{cloud}')

    cloud_to_run = _get_cloud_to_run(config)

    for item in items:
        if 'slow' in item.keywords and not config.getoption('--runslow'):
            item.add_marker(skip_marks['slow'])
        for cloud in all_clouds_in_smoke_tests:
            cloud_keyword = cloud_to_pytest_keyword[cloud]
            if ((f'no_{cloud_keyword}' in item.keywords and
                 cloud in cloud_to_run) or
                (cloud_keyword in item.keywords and cloud not in cloud_to_run)):
                item.add_marker(skip_marks[cloud])

        if (not 'managed_spot'
                in item.keywords) and config.getoption('--managed-spot'):
            item.add_marker(skip_marks['managed_spot'])

    # We run Lambda Cloud tests serially because Lambda Cloud rate limits its
    # launch API to one launch every 10 seconds.
    serial_mark = pytest.mark.xdist_group(name='serial_lambda_cloud')
    # Handle generic tests
    if _generic_cloud(config) == 'lambda':
        for item in items:
            if (_is_generic_test(item) and
                    'no_lambda_cloud' not in item.keywords):
                item.add_marker(serial_mark)
                # Adding the serial mark does not update the item.nodeid,
                # but item.nodeid is important for pytest.xdist_group, e.g.
                #   https://github.com/pytest-dev/pytest-xdist/blob/master/src/xdist/scheduler/loadgroup.py
                # This is a hack to update item.nodeid
                item._nodeid = f'{item.nodeid}@serial_lambda_cloud'
    # Handle Lambda Cloud specific tests
    for item in items:
        if 'lambda_cloud' in item.keywords:
            item.add_marker(serial_mark)
            item._nodeid = f'{item.nodeid}@serial_lambda_cloud'  # See comment on item.nodeid above


def _is_generic_test(item) -> bool:
    for cloud in all_clouds_in_smoke_tests:
        if cloud_to_pytest_keyword[cloud] in item.keywords:
            return False
    return True


def _generic_cloud(config) -> str:
    c = config.getoption('--generic-cloud')
    cloud_to_run = _get_cloud_to_run(config)
    if c not in cloud_to_run:
        c = cloud_to_run[0]
    return c


@pytest.fixture
def generic_cloud(request) -> str:
    return _generic_cloud(request.config)


def pytest_sessionstart(session):
    from sky.clouds.service_catalog import common
    aws_az_mapping_path = common.get_catalog_path('aws/az_mappings.csv')

    if not os.path.exists(aws_az_mapping_path):
        try:
            # Try to fetch the AZ mapping from AWS (if we have AWS access)
            from sky.clouds.service_catalog import aws_catalog
        except:
            # If we don't have AWS access, create a dummy file
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                f.write(
                    textwrap.dedent("""\
                    AvailabilityZoneName,AvailabilityZone
                    us-east-1a,use1-az2
                    us-east-1b,use1-az4
                    us-east-1c,use1-az6
                    us-east-1d,use1-az1
                    us-east-1e,use1-az3
                    us-east-1f,use1-az5
                    us-east-2a,use2-az1
                    us-east-2b,use2-az2
                    us-east-2c,use2-az3
                    us-west-1a,usw1-az1
                    us-west-1c,usw1-az3
                    us-west-2a,usw2-az1
                    us-west-2b,usw2-az2
                    us-west-2c,usw2-az3
                    us-west-2d,usw2-az4
                    ca-central-1a,cac1-az1
                    ca-central-1b,cac1-az2
                    ca-central-1d,cac1-az4
                    eu-central-1a,euc1-az2
                    eu-central-1b,euc1-az3
                    eu-central-1c,euc1-az1
                    eu-west-1a,euw1-az3
                    eu-west-1b,euw1-az1
                    eu-west-1c,euw1-az2
                    eu-west-2a,euw2-az2
                    eu-west-2b,euw2-az3
                    eu-west-2c,euw2-az1
                    eu-west-3a,euw3-az1
                    eu-west-3b,euw3-az2
                    eu-west-3c,euw3-az3
                    eu-north-1a,eun1-az1
                    eu-north-1b,eun1-az2
                    eu-north-1c,eun1-az3
                    ap-south-1a,aps1-az1
                    ap-south-1b,aps1-az3
                    ap-south-1c,aps1-az2
                    ap-northeast-3a,apne3-az3
                    ap-northeast-3b,apne3-az1
                    ap-northeast-3c,apne3-az2
                    ap-northeast-2a,apne2-az1
                    ap-northeast-2b,apne2-az2
                    ap-northeast-2c,apne2-az3
                    ap-northeast-2d,apne2-az4
                    ap-southeast-1a,apse1-az2
                    ap-southeast-1b,apse1-az1
                    ap-southeast-1c,apse1-az3
                    ap-southeast-2a,apse2-az1
                    ap-southeast-2b,apse2-az3
                    ap-southeast-2c,apse2-az2
                    ap-northeast-1a,apne1-az4
                    ap-northeast-1c,apne1-az1
                    ap-northeast-1d,apne1-az2
                """))
            os.replace(f.name, aws_az_mapping_path)
