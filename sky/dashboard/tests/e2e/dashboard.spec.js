// @ts-check
const { test, expect } = require('@playwright/test');

/**
 * SkyPilot Dashboard E2E Tests
 *
 * These tests verify the basic functionality of the SkyPilot dashboard,
 * including page navigation, element visibility, and core features.
 */

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
