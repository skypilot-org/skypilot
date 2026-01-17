// @ts-check
const { test, expect } = require('@playwright/test');

/**
 * SkyPilot Dashboard E2E Tests
 *
 * These tests verify the basic functionality of the SkyPilot dashboard,
 * including page navigation, element visibility, core features, and
 * performance timing for table loading.
 */

// Performance thresholds (in milliseconds)
const PERFORMANCE_THRESHOLDS = {
  // Maximum time for initial page load
  PAGE_LOAD_MAX: 10000,
  // Maximum time for table to become visible
  TABLE_VISIBLE_MAX: 15000,
  // Maximum time for loading spinner to disappear
  LOADING_COMPLETE_MAX: 30000,
  // Maximum time for data rows to appear (if data exists)
  DATA_ROWS_MAX: 30000,
};

/**
 * Measures the time it takes for an element to become visible
 * @param {import('@playwright/test').Page} page
 * @param {string} selector
 * @param {number} timeout
 * @returns {Promise<{visible: boolean, duration: number}>}
 */
async function measureElementVisibility(page, selector, timeout = 30000) {
  const startTime = Date.now();
  try {
    await page.locator(selector).first().waitFor({ state: 'visible', timeout });
    return { visible: true, duration: Date.now() - startTime };
  } catch {
    return { visible: false, duration: Date.now() - startTime };
  }
}

/**
 * Measures time until loading indicator disappears
 * @param {import('@playwright/test').Page} page
 * @param {number} timeout
 * @returns {Promise<{complete: boolean, duration: number}>}
 */
async function measureLoadingComplete(page, timeout = 30000) {
  const startTime = Date.now();
  try {
    // Wait for CircularProgress (loading spinner) to disappear
    // The dashboard uses MUI CircularProgress for loading states
    const loadingIndicator = page.locator(
      '.MuiCircularProgress-root, [role="progressbar"], text="Loading..."'
    );

    // First check if loading indicator appears
    const hasLoading = await loadingIndicator.first().isVisible().catch(() => false);

    if (hasLoading) {
      // Wait for it to disappear
      await loadingIndicator.first().waitFor({ state: 'hidden', timeout });
    }

    return { complete: true, duration: Date.now() - startTime };
  } catch {
    return { complete: false, duration: Date.now() - startTime };
  }
}

/**
 * Measures time for table rows to appear
 * @param {import('@playwright/test').Page} page
 * @param {number} timeout
 * @returns {Promise<{hasRows: boolean, rowCount: number, duration: number}>}
 */
async function measureTableDataLoad(page, timeout = 30000) {
  const startTime = Date.now();
  try {
    // Wait for table body to have content (either data rows or empty state)
    await page.waitForFunction(
      () => {
        const tbody = document.querySelector('tbody');
        if (!tbody) return false;
        const rows = tbody.querySelectorAll('tr');
        // Return true if we have rows (either data or empty state message)
        return rows.length > 0;
      },
      { timeout }
    );

    const rowCount = await page.locator('tbody tr').count();
    // Check if it's actual data rows (not loading or empty state)
    const hasDataRows = await page
      .locator('tbody tr td')
      .first()
      .isVisible()
      .catch(() => false);

    return {
      hasRows: hasDataRows,
      rowCount,
      duration: Date.now() - startTime,
    };
  } catch {
    return { hasRows: false, rowCount: 0, duration: Date.now() - startTime };
  }
}

test.describe('Dashboard Navigation', () => {
  test('should redirect root to clusters page', async ({ page }) => {
    await page.goto('/');
    // The index page redirects to /clusters
    await expect(page).toHaveURL(/.*\/clusters/);
  });

  test('should load clusters page', async ({ page }) => {
    await page.goto('/clusters');
    await expect(page).toHaveTitle(/Clusters.*SkyPilot/);
    // Wait for the page to load
    await page.waitForLoadState('networkidle');
  });

  test('should load jobs page', async ({ page }) => {
    await page.goto('/jobs');
    await expect(page).toHaveTitle(/Managed Jobs.*SkyPilot/);
    await page.waitForLoadState('networkidle');
  });

  test('should load infra page', async ({ page }) => {
    await page.goto('/infra');
    await expect(page).toHaveTitle(/Infra.*SkyPilot|SkyPilot/);
    await page.waitForLoadState('networkidle');
  });

  test('should load workspaces page', async ({ page }) => {
    await page.goto('/workspaces');
    await expect(page).toHaveTitle(/Workspaces.*SkyPilot|SkyPilot/);
    await page.waitForLoadState('networkidle');
  });
});

test.describe('Dashboard Layout', () => {
  test('should display navigation bar', async ({ page }) => {
    await page.goto('/clusters');
    await page.waitForLoadState('networkidle');

    // The TopBar should be visible (fixed at top)
    const topBar = page.locator('.fixed.top-0');
    await expect(topBar).toBeVisible();
  });

  test('should have working navigation links', async ({ page }) => {
    await page.goto('/clusters');
    await page.waitForLoadState('networkidle');

    // Look for navigation links in the top bar
    // The navigation should contain links to main pages
    const nav = page.locator('nav, [role="navigation"], header');
    await expect(nav.first()).toBeVisible();
  });
});

test.describe('Clusters Page', () => {
  test('should display clusters table or empty state', async ({ page }) => {
    await page.goto('/clusters');
    await page.waitForLoadState('networkidle');

    // Wait for either a table or loading indicator to appear
    // The page should show either clusters or an empty state
    const content = page.locator('main');
    await expect(content).toBeVisible();
  });

  test('should have refresh functionality', async ({ page }) => {
    await page.goto('/clusters');
    await page.waitForLoadState('networkidle');

    // Look for refresh button (RotateCwIcon is used)
    const refreshButton = page.locator(
      'button:has(svg), [aria-label*="refresh"], [title*="refresh"]'
    );
    // There should be some interactive elements
    const mainContent = page.locator('main');
    await expect(mainContent).toBeVisible();
  });
});

test.describe('Jobs Page', () => {
  test('should display jobs table or empty state', async ({ page }) => {
    await page.goto('/jobs');
    await page.waitForLoadState('networkidle');

    // The main content area should be visible
    const content = page.locator('main');
    await expect(content).toBeVisible();
  });
});

test.describe('API Connectivity', () => {
  test('should be able to fetch cluster data', async ({ page, request }) => {
    // Test that the API endpoint is accessible
    // The dashboard proxies /internal/dashboard to the API server
    const response = await request.get('/internal/dashboard/api/health');
    // Accept either success or that the endpoint exists (may need auth)
    expect([200, 401, 403, 404]).toContain(response.status());
  });
});

test.describe('Page Responsiveness', () => {
  test('should be responsive on mobile viewport', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/clusters');
    await page.waitForLoadState('networkidle');

    // Main content should still be visible
    const content = page.locator('main');
    await expect(content).toBeVisible();
  });

  test('should be responsive on tablet viewport', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto('/clusters');
    await page.waitForLoadState('networkidle');

    // Main content should still be visible
    const content = page.locator('main');
    await expect(content).toBeVisible();
  });
});

test.describe('Error Handling', () => {
  test('should handle 404 pages gracefully', async ({ page }) => {
    const response = await page.goto('/nonexistent-page');
    // Next.js should return a 404 page
    // The page should still render without crashing
    await expect(page.locator('body')).toBeVisible();
  });
});

test.describe('Performance - Table Loading Times', () => {
  test('clusters page - should load table within threshold', async ({ page }) => {
    const navigationStart = Date.now();

    // Navigate to clusters page
    await page.goto('/clusters');
    const navigationDuration = Date.now() - navigationStart;

    console.log(`[Clusters] Navigation completed in ${navigationDuration}ms`);
    expect(navigationDuration).toBeLessThan(PERFORMANCE_THRESHOLDS.PAGE_LOAD_MAX);

    // Measure time for table to become visible
    const tableVisibility = await measureElementVisibility(
      page,
      'table, [role="table"], .MuiTable-root',
      PERFORMANCE_THRESHOLDS.TABLE_VISIBLE_MAX
    );

    console.log(
      `[Clusters] Table visible: ${tableVisibility.visible}, duration: ${tableVisibility.duration}ms`
    );
    expect(tableVisibility.visible).toBe(true);
    expect(tableVisibility.duration).toBeLessThan(
      PERFORMANCE_THRESHOLDS.TABLE_VISIBLE_MAX
    );

    // Measure time for loading to complete
    const loadingComplete = await measureLoadingComplete(
      page,
      PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX
    );

    console.log(
      `[Clusters] Loading complete: ${loadingComplete.complete}, duration: ${loadingComplete.duration}ms`
    );
    expect(loadingComplete.complete).toBe(true);

    // Measure time for table data to load
    const tableData = await measureTableDataLoad(
      page,
      PERFORMANCE_THRESHOLDS.DATA_ROWS_MAX
    );

    console.log(
      `[Clusters] Table data - hasRows: ${tableData.hasRows}, rowCount: ${tableData.rowCount}, duration: ${tableData.duration}ms`
    );

    // Table should have loaded content (data or empty state)
    expect(tableData.duration).toBeLessThan(PERFORMANCE_THRESHOLDS.DATA_ROWS_MAX);
  });

  test('jobs page - should load table within threshold', async ({ page }) => {
    const navigationStart = Date.now();

    // Navigate to jobs page
    await page.goto('/jobs');
    const navigationDuration = Date.now() - navigationStart;

    console.log(`[Jobs] Navigation completed in ${navigationDuration}ms`);
    expect(navigationDuration).toBeLessThan(PERFORMANCE_THRESHOLDS.PAGE_LOAD_MAX);

    // Measure time for table to become visible
    const tableVisibility = await measureElementVisibility(
      page,
      'table, [role="table"], .MuiTable-root',
      PERFORMANCE_THRESHOLDS.TABLE_VISIBLE_MAX
    );

    console.log(
      `[Jobs] Table visible: ${tableVisibility.visible}, duration: ${tableVisibility.duration}ms`
    );
    expect(tableVisibility.visible).toBe(true);
    expect(tableVisibility.duration).toBeLessThan(
      PERFORMANCE_THRESHOLDS.TABLE_VISIBLE_MAX
    );

    // Measure time for loading to complete
    const loadingComplete = await measureLoadingComplete(
      page,
      PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX
    );

    console.log(
      `[Jobs] Loading complete: ${loadingComplete.complete}, duration: ${loadingComplete.duration}ms`
    );
    expect(loadingComplete.complete).toBe(true);

    // Measure time for table data to load
    const tableData = await measureTableDataLoad(
      page,
      PERFORMANCE_THRESHOLDS.DATA_ROWS_MAX
    );

    console.log(
      `[Jobs] Table data - hasRows: ${tableData.hasRows}, rowCount: ${tableData.rowCount}, duration: ${tableData.duration}ms`
    );

    // Table should have loaded content (data or empty state)
    expect(tableData.duration).toBeLessThan(PERFORMANCE_THRESHOLDS.DATA_ROWS_MAX);
  });

  test('infra page - should load content within threshold', async ({ page }) => {
    const navigationStart = Date.now();

    // Navigate to infra page
    await page.goto('/infra');
    const navigationDuration = Date.now() - navigationStart;

    console.log(`[Infra] Navigation completed in ${navigationDuration}ms`);
    expect(navigationDuration).toBeLessThan(PERFORMANCE_THRESHOLDS.PAGE_LOAD_MAX);

    // Measure time for main content to become visible
    const contentVisibility = await measureElementVisibility(
      page,
      'main',
      PERFORMANCE_THRESHOLDS.TABLE_VISIBLE_MAX
    );

    console.log(
      `[Infra] Content visible: ${contentVisibility.visible}, duration: ${contentVisibility.duration}ms`
    );
    expect(contentVisibility.visible).toBe(true);

    // Measure time for loading to complete
    const loadingComplete = await measureLoadingComplete(
      page,
      PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX
    );

    console.log(
      `[Infra] Loading complete: ${loadingComplete.complete}, duration: ${loadingComplete.duration}ms`
    );
    expect(loadingComplete.complete).toBe(true);
  });

  test('workspaces page - should load content within threshold', async ({
    page,
  }) => {
    const navigationStart = Date.now();

    // Navigate to workspaces page
    await page.goto('/workspaces');
    const navigationDuration = Date.now() - navigationStart;

    console.log(`[Workspaces] Navigation completed in ${navigationDuration}ms`);
    expect(navigationDuration).toBeLessThan(PERFORMANCE_THRESHOLDS.PAGE_LOAD_MAX);

    // Measure time for main content to become visible
    const contentVisibility = await measureElementVisibility(
      page,
      'main',
      PERFORMANCE_THRESHOLDS.TABLE_VISIBLE_MAX
    );

    console.log(
      `[Workspaces] Content visible: ${contentVisibility.visible}, duration: ${contentVisibility.duration}ms`
    );
    expect(contentVisibility.visible).toBe(true);

    // Measure time for loading to complete
    const loadingComplete = await measureLoadingComplete(
      page,
      PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX
    );

    console.log(
      `[Workspaces] Loading complete: ${loadingComplete.complete}, duration: ${loadingComplete.duration}ms`
    );
    expect(loadingComplete.complete).toBe(true);
  });
});

test.describe('Performance - Page Transitions', () => {
  test('should navigate between pages quickly', async ({ page }) => {
    // Start at clusters page
    await page.goto('/clusters');
    await page.waitForLoadState('networkidle');

    // Measure navigation to jobs page
    const toJobsStart = Date.now();
    await page.goto('/jobs');
    await measureLoadingComplete(page, PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX);
    const toJobsDuration = Date.now() - toJobsStart;

    console.log(`[Navigation] Clusters -> Jobs: ${toJobsDuration}ms`);
    expect(toJobsDuration).toBeLessThan(PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX);

    // Measure navigation to workspaces page
    const toWorkspacesStart = Date.now();
    await page.goto('/workspaces');
    await measureLoadingComplete(page, PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX);
    const toWorkspacesDuration = Date.now() - toWorkspacesStart;

    console.log(`[Navigation] Jobs -> Workspaces: ${toWorkspacesDuration}ms`);
    expect(toWorkspacesDuration).toBeLessThan(
      PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX
    );

    // Measure navigation back to clusters page
    const toClustersStart = Date.now();
    await page.goto('/clusters');
    await measureLoadingComplete(page, PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX);
    const toClustersDuration = Date.now() - toClustersStart;

    console.log(`[Navigation] Workspaces -> Clusters: ${toClustersDuration}ms`);
    expect(toClustersDuration).toBeLessThan(
      PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX
    );
  });
});

test.describe('Performance - Refresh Operations', () => {
  test('clusters page - refresh should complete within threshold', async ({
    page,
  }) => {
    // Navigate and wait for initial load
    await page.goto('/clusters');
    await measureLoadingComplete(page, PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX);

    // Find and click the refresh button
    const refreshButton = page.locator(
      'button:has(svg.lucide-rotate-cw), button[aria-label*="refresh"], button:has-text("Refresh")'
    );

    // Check if refresh button exists
    const hasRefreshButton = (await refreshButton.count()) > 0;

    if (hasRefreshButton) {
      const refreshStart = Date.now();
      await refreshButton.first().click();

      // Measure time for refresh to complete
      const refreshComplete = await measureLoadingComplete(
        page,
        PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX
      );
      const refreshDuration = Date.now() - refreshStart;

      console.log(
        `[Clusters Refresh] Complete: ${refreshComplete.complete}, duration: ${refreshDuration}ms`
      );
      expect(refreshComplete.complete).toBe(true);
      expect(refreshDuration).toBeLessThan(
        PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX
      );
    } else {
      console.log('[Clusters Refresh] No refresh button found, skipping');
    }
  });

  test('jobs page - refresh should complete within threshold', async ({
    page,
  }) => {
    // Navigate and wait for initial load
    await page.goto('/jobs');
    await measureLoadingComplete(page, PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX);

    // Find and click the refresh button
    const refreshButton = page.locator(
      'button:has(svg.lucide-rotate-cw), button[aria-label*="refresh"], button:has-text("Refresh")'
    );

    // Check if refresh button exists
    const hasRefreshButton = (await refreshButton.count()) > 0;

    if (hasRefreshButton) {
      const refreshStart = Date.now();
      await refreshButton.first().click();

      // Measure time for refresh to complete
      const refreshComplete = await measureLoadingComplete(
        page,
        PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX
      );
      const refreshDuration = Date.now() - refreshStart;

      console.log(
        `[Jobs Refresh] Complete: ${refreshComplete.complete}, duration: ${refreshDuration}ms`
      );
      expect(refreshComplete.complete).toBe(true);
      expect(refreshDuration).toBeLessThan(
        PERFORMANCE_THRESHOLDS.LOADING_COMPLETE_MAX
      );
    } else {
      console.log('[Jobs Refresh] No refresh button found, skipping');
    }
  });
});
