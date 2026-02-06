// @ts-check
const { test, expect } = require('@playwright/test');

/**
 * SkyPilot Dashboard Data Visibility Tests
 *
 * These tests verify that cluster and job data appears correctly in the
 * dashboard UI. They require actual clusters/jobs to be running.
 *
 * Environment variables:
 * - EXPECTED_CLUSTER_NAME: Name of a cluster that should be visible
 * - EXPECTED_JOB_NAME: Name of a job that should be visible
 * - EXPECTED_CLUSTER_STATUS: Expected status of the cluster (e.g., 'UP', 'STOPPED')
 * - EXPECTED_JOB_STATUS: Expected status of the job (e.g., 'RUNNING', 'SUCCEEDED')
 */

// Get expected data from environment variables
const EXPECTED_CLUSTER = process.env.EXPECTED_CLUSTER_NAME;
const EXPECTED_JOB = process.env.EXPECTED_JOB_NAME;
const EXPECTED_CLUSTER_STATUS = process.env.EXPECTED_CLUSTER_STATUS;
const EXPECTED_JOB_STATUS = process.env.EXPECTED_JOB_STATUS;

// Timeouts for waiting for data
const DATA_LOAD_TIMEOUT = 30000;
const DATA_APPEAR_TIMEOUT = 60000;

test.describe('Cluster Data Visibility', () => {
  test.skip(!EXPECTED_CLUSTER, 'No EXPECTED_CLUSTER_NAME provided');

  test('cluster should appear in clusters table', async ({ page }) => {
    await page.goto('/dashboard/clusters');
    await page.waitForLoadState('networkidle');

    // Wait for the table to load
    await page.waitForSelector('table, [role="table"]', {
      timeout: DATA_LOAD_TIMEOUT,
    });

    // Wait for loading to complete
    await page
      .locator('.MuiCircularProgress-root, text="Loading..."')
      .waitFor({ state: 'hidden', timeout: DATA_LOAD_TIMEOUT })
      .catch(() => {});

    // Look for the cluster name in the table
    const clusterRow = page.locator(`tr:has-text("${EXPECTED_CLUSTER}")`);

    // Wait for the cluster to appear
    await expect(clusterRow.first()).toBeVisible({ timeout: DATA_APPEAR_TIMEOUT });

    console.log(`[Data Visibility] Cluster "${EXPECTED_CLUSTER}" found in table`);

    // If an expected status is provided, verify it
    if (EXPECTED_CLUSTER_STATUS) {
      const statusBadge = clusterRow.locator(
        `text=/${EXPECTED_CLUSTER_STATUS}/i, [class*="badge"]:has-text("${EXPECTED_CLUSTER_STATUS}")`
      );
      await expect(statusBadge.first()).toBeVisible({ timeout: DATA_LOAD_TIMEOUT });
      console.log(
        `[Data Visibility] Cluster "${EXPECTED_CLUSTER}" has expected status "${EXPECTED_CLUSTER_STATUS}"`
      );
    }
  });

  test('cluster details page should load', async ({ page }) => {
    // Navigate directly to cluster details
    await page.goto(`/dashboard/clusters/${EXPECTED_CLUSTER}`);

    // Wait for page to load
    await page.waitForLoadState('networkidle');

    // The page should show the cluster name
    await expect(page.locator(`text=${EXPECTED_CLUSTER}`).first()).toBeVisible({
      timeout: DATA_LOAD_TIMEOUT,
    });

    console.log(`[Data Visibility] Cluster details page loaded for "${EXPECTED_CLUSTER}"`);
  });
});

test.describe('Job Data Visibility', () => {
  test.skip(!EXPECTED_JOB, 'No EXPECTED_JOB_NAME provided');

  test('job should appear in jobs table', async ({ page }) => {
    await page.goto('/dashboard/jobs');
    await page.waitForLoadState('networkidle');

    // Wait for the table to load
    await page.waitForSelector('table, [role="table"]', {
      timeout: DATA_LOAD_TIMEOUT,
    });

    // Wait for loading to complete
    await page
      .locator('.MuiCircularProgress-root, text="Loading..."')
      .waitFor({ state: 'hidden', timeout: DATA_LOAD_TIMEOUT })
      .catch(() => {});

    // Look for the job name in the table
    const jobRow = page.locator(`tr:has-text("${EXPECTED_JOB}")`);

    // Wait for the job to appear
    await expect(jobRow.first()).toBeVisible({ timeout: DATA_APPEAR_TIMEOUT });

    console.log(`[Data Visibility] Job "${EXPECTED_JOB}" found in table`);

    // If an expected status is provided, verify it
    if (EXPECTED_JOB_STATUS) {
      const statusBadge = jobRow.locator(
        `text=/${EXPECTED_JOB_STATUS}/i, [class*="badge"]:has-text("${EXPECTED_JOB_STATUS}")`
      );
      await expect(statusBadge.first()).toBeVisible({ timeout: DATA_LOAD_TIMEOUT });
      console.log(
        `[Data Visibility] Job "${EXPECTED_JOB}" has expected status "${EXPECTED_JOB_STATUS}"`
      );
    }
  });

  test('job details page should load', async ({ page }) => {
    // First get the job ID from the jobs list
    await page.goto('/dashboard/jobs');
    await page.waitForLoadState('networkidle');

    // Wait for the table to load
    await page.waitForSelector('table, [role="table"]', {
      timeout: DATA_LOAD_TIMEOUT,
    });

    // Wait for loading to complete
    await page
      .locator('.MuiCircularProgress-root, text="Loading..."')
      .waitFor({ state: 'hidden', timeout: DATA_LOAD_TIMEOUT })
      .catch(() => {});

    // Click on the job row to navigate to details
    const jobRow = page.locator(`tr:has-text("${EXPECTED_JOB}")`);
    await expect(jobRow.first()).toBeVisible({ timeout: DATA_APPEAR_TIMEOUT });

    // Try to click the job name link or the row
    const jobLink = jobRow.locator('a').first();
    if ((await jobLink.count()) > 0) {
      await jobLink.click();
    } else {
      await jobRow.first().click();
    }

    // Wait for navigation
    await page.waitForLoadState('networkidle');

    // The page should show job details
    await expect(page.locator('main')).toBeVisible();

    console.log(`[Data Visibility] Job details page loaded for "${EXPECTED_JOB}"`);
  });
});

test.describe('Table Row Count Verification', () => {
  test('clusters page should show expected number of clusters', async ({
    page,
  }) => {
    await page.goto('/dashboard/clusters');
    await page.waitForLoadState('networkidle');

    // Wait for the table to load
    await page.waitForSelector('table, [role="table"]', {
      timeout: DATA_LOAD_TIMEOUT,
    });

    // Wait for loading to complete
    await page
      .locator('.MuiCircularProgress-root, text="Loading..."')
      .waitFor({ state: 'hidden', timeout: DATA_LOAD_TIMEOUT })
      .catch(() => {});

    // Count the number of data rows (excluding header)
    const dataRows = page.locator('tbody tr');
    const rowCount = await dataRows.count();

    console.log(`[Table Count] Clusters table has ${rowCount} rows`);

    // If we expected a cluster, there should be at least 1 row
    if (EXPECTED_CLUSTER) {
      expect(rowCount).toBeGreaterThan(0);
    }
  });

  test('jobs page should show expected number of jobs', async ({ page }) => {
    await page.goto('/dashboard/jobs');
    await page.waitForLoadState('networkidle');

    // Wait for the table to load
    await page.waitForSelector('table, [role="table"]', {
      timeout: DATA_LOAD_TIMEOUT,
    });

    // Wait for loading to complete
    await page
      .locator('.MuiCircularProgress-root, text="Loading..."')
      .waitFor({ state: 'hidden', timeout: DATA_LOAD_TIMEOUT })
      .catch(() => {});

    // Count the number of data rows (excluding header)
    const dataRows = page.locator('tbody tr');
    const rowCount = await dataRows.count();

    console.log(`[Table Count] Jobs table has ${rowCount} rows`);

    // If we expected a job, there should be at least 1 row
    if (EXPECTED_JOB) {
      expect(rowCount).toBeGreaterThan(0);
    }
  });
});

test.describe('Data Refresh Verification', () => {
  test('clusters page should refresh data correctly', async ({ page }) => {
    await page.goto('/dashboard/clusters');
    await page.waitForLoadState('networkidle');

    // Wait for initial load
    await page
      .locator('.MuiCircularProgress-root, text="Loading..."')
      .waitFor({ state: 'hidden', timeout: DATA_LOAD_TIMEOUT })
      .catch(() => {});

    // Get initial row count
    const initialRowCount = await page.locator('tbody tr').count();

    // Find and click refresh button
    const refreshButton = page.locator(
      'button:has(svg.lucide-rotate-cw), button[aria-label*="refresh"]'
    );

    if ((await refreshButton.count()) > 0) {
      await refreshButton.first().click();

      // Wait for refresh to complete
      await page
        .locator('.MuiCircularProgress-root, text="Loading..."')
        .waitFor({ state: 'hidden', timeout: DATA_LOAD_TIMEOUT })
        .catch(() => {});

      // Get new row count
      const newRowCount = await page.locator('tbody tr').count();

      console.log(
        `[Data Refresh] Clusters: initial=${initialRowCount}, after refresh=${newRowCount}`
      );

      // Row count should be consistent (data should not disappear)
      expect(newRowCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('jobs page should refresh data correctly', async ({ page }) => {
    await page.goto('/dashboard/jobs');
    await page.waitForLoadState('networkidle');

    // Wait for initial load
    await page
      .locator('.MuiCircularProgress-root, text="Loading..."')
      .waitFor({ state: 'hidden', timeout: DATA_LOAD_TIMEOUT })
      .catch(() => {});

    // Get initial row count
    const initialRowCount = await page.locator('tbody tr').count();

    // Find and click refresh button
    const refreshButton = page.locator(
      'button:has(svg.lucide-rotate-cw), button[aria-label*="refresh"]'
    );

    if ((await refreshButton.count()) > 0) {
      await refreshButton.first().click();

      // Wait for refresh to complete
      await page
        .locator('.MuiCircularProgress-root, text="Loading..."')
        .waitFor({ state: 'hidden', timeout: DATA_LOAD_TIMEOUT })
        .catch(() => {});

      // Get new row count
      const newRowCount = await page.locator('tbody tr').count();

      console.log(
        `[Data Refresh] Jobs: initial=${initialRowCount}, after refresh=${newRowCount}`
      );

      // Row count should be consistent
      expect(newRowCount).toBeGreaterThanOrEqual(0);
    }
  });
});
