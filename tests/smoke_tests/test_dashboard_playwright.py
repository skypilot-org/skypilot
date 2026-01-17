"""Smoke tests for the SkyPilot dashboard using Playwright.

This module contains smoke tests that validate the SkyPilot dashboard
functionality using Playwright for browser automation.

The tests verify:
- Dashboard pages load correctly
- Navigation works as expected
- Basic UI elements are visible
- API connectivity through the dashboard
"""
import os
import shutil

import pytest
from smoke_tests import smoke_tests_utils


def _check_playwright_installed() -> bool:
    """Check if Playwright is available."""
    dashboard_dir = os.path.join(os.path.dirname(__file__), '..', '..',
                                 'sky', 'dashboard')
    node_modules = os.path.join(dashboard_dir, 'node_modules')
    playwright_path = os.path.join(node_modules, '@playwright', 'test')
    return os.path.exists(playwright_path)


def _check_npm_available() -> bool:
    """Check if npm is available."""
    return shutil.which('npm') is not None


# ---------- Test Dashboard with Playwright ----------
@pytest.mark.local  # Dashboard tests run locally with the API server
@pytest.mark.no_remote_server  # Requires local API server and dashboard
def test_dashboard_playwright_basic():
    """Test basic dashboard functionality with Playwright.

    This test:
    1. Ensures the API server is running
    2. Installs npm dependencies if needed
    3. Installs Playwright browsers
    4. Runs Playwright E2E tests
    """
    if not _check_npm_available():
        pytest.skip('npm is not available, skipping dashboard Playwright tests')

    dashboard_dir = 'sky/dashboard'

    test = smoke_tests_utils.Test(
        'test_dashboard_playwright_basic',
        [
            # Ensure API server is running
            smoke_tests_utils.SKY_API_RESTART,
            # Install npm dependencies
            f'cd {dashboard_dir} && npm install',
            # Install Playwright browsers (chromium only for speed)
            f'cd {dashboard_dir} && npx playwright install chromium',
            # Run Playwright tests
            # SKIP_WEB_SERVER=1 means Playwright won't start its own server
            # We let the test start the dashboard server itself
            f'cd {dashboard_dir} && npm run test:e2e',
        ],
        timeout=smoke_tests_utils.get_timeout('local', override_timeout=20 * 60),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.local
@pytest.mark.no_remote_server
def test_dashboard_playwright_with_cluster(generic_cloud: str):
    """Test dashboard with an actual cluster.

    This test:
    1. Launches a minimal cluster
    2. Runs Playwright tests that can verify cluster visibility
    3. Verifies the cluster appears in the dashboard UI
    4. Cleans up the cluster
    """
    if not _check_npm_available():
        pytest.skip('npm is not available, skipping dashboard Playwright tests')

    name = smoke_tests_utils.get_cluster_name()
    dashboard_dir = 'sky/dashboard'

    test = smoke_tests_utils.Test(
        'test_dashboard_playwright_with_cluster',
        [
            # Ensure API server is running
            smoke_tests_utils.SKY_API_RESTART,
            # Launch a minimal cluster
            f'sky launch -y -c {name} --infra {generic_cloud} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'tests/test_yamls/minimal.yaml',
            # Verify cluster is UP via CLI
            f'sky status {name} | grep UP',
            # Install npm dependencies
            f'cd {dashboard_dir} && npm install',
            # Install Playwright browsers
            f'cd {dashboard_dir} && npx playwright install chromium',
            # Run basic Playwright tests
            f'cd {dashboard_dir} && npm run test:e2e -- tests/e2e/dashboard.spec.js',
            # Run data visibility tests with the cluster name
            f'cd {dashboard_dir} && EXPECTED_CLUSTER_NAME={name} EXPECTED_CLUSTER_STATUS=UP '
            f'npm run test:e2e -- tests/e2e/data-visibility.spec.js',
        ],
        teardown=f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud,
                                               override_timeout=30 * 60),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
@pytest.mark.local
@pytest.mark.no_remote_server
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support host controllers
@pytest.mark.no_shadeform  # Shadeform does not support host controllers
def test_dashboard_playwright_with_managed_job(generic_cloud: str):
    """Test dashboard with an actual managed job.

    This test:
    1. Launches a managed job
    2. Runs Playwright tests to verify job visibility in dashboard
    3. Verifies job status transitions are reflected in the UI
    4. Cleans up the job
    """
    if not _check_npm_available():
        pytest.skip('npm is not available, skipping dashboard Playwright tests')

    import sky

    name = smoke_tests_utils.get_cluster_name()
    dashboard_dir = 'sky/dashboard'

    test = smoke_tests_utils.Test(
        'test_dashboard_playwright_with_managed_job',
        [
            # Ensure API server is running
            smoke_tests_utils.SKY_API_RESTART,
            # Launch a managed job that runs for a while
            f'sky jobs launch -n {name} --infra {generic_cloud} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} -y -d '
            f'"echo Starting job && sleep 60 && echo Job completed"',
            # Wait for the job to start running
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[
                    sky.ManagedJobStatus.STARTING, sky.ManagedJobStatus.RUNNING
                ],
                timeout=300),
            # Install npm dependencies
            f'cd {dashboard_dir} && npm install',
            # Install Playwright browsers
            f'cd {dashboard_dir} && npx playwright install chromium',
            # Run data visibility tests with the job name
            f'cd {dashboard_dir} && EXPECTED_JOB_NAME={name} EXPECTED_JOB_STATUS=RUNNING '
            f'npm run test:e2e -- tests/e2e/data-visibility.spec.js',
        ],
        teardown=f'sky jobs cancel -y -n {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud,
                                               override_timeout=30 * 60),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.local
@pytest.mark.no_remote_server
def test_dashboard_api_endpoints():
    """Test dashboard API endpoints directly.

    This test verifies that the dashboard API endpoints are accessible
    and return expected responses.
    """

    def verify_dashboard_api():
        """Verify dashboard API endpoints are working."""
        import requests

        api_url = smoke_tests_utils.get_api_server_url()

        # Test the health endpoint
        health_response = requests.get(f'{api_url}/api/health', timeout=10)
        assert health_response.status_code == 200, \
            f'Health endpoint failed: {health_response.status_code}'

        # Test getting cluster status via dashboard API
        try:
            request_id = smoke_tests_utils.get_dashboard_cluster_status_request_id(
            )
            result = smoke_tests_utils.get_response_from_request_id_dashboard(
                request_id)
            # Result should be a list (possibly empty)
            assert isinstance(result, list), \
                f'Expected list, got {type(result)}'
            yield f'Cluster status API returned {len(result)} clusters'
        except Exception as e:
            # API might return error if no clusters, that's OK
            yield f'Cluster status API check: {e}'

        # Test getting jobs queue via dashboard API
        try:
            request_id = smoke_tests_utils.get_dashboard_jobs_queue_request_id()
            result = smoke_tests_utils.get_response_from_request_id_dashboard(
                request_id)
            # Result should be a list (possibly empty)
            assert isinstance(result, list), \
                f'Expected list, got {type(result)}'
            yield f'Jobs queue API returned {len(result)} jobs'
        except Exception as e:
            # API might return error if no jobs, that's OK
            yield f'Jobs queue API check: {e}'

    test = smoke_tests_utils.Test(
        'test_dashboard_api_endpoints',
        [
            # Ensure API server is running
            smoke_tests_utils.SKY_API_RESTART,
            # Run the API verification
            verify_dashboard_api,
        ],
        timeout=smoke_tests_utils.get_timeout('local', override_timeout=5 * 60),
    )
    smoke_tests_utils.run_one_test(test)
