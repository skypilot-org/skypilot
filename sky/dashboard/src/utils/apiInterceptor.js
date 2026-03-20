'use client';

/**
 * Check if a request is for a static asset (scripts, styles, images, etc.)
 * These should be ignored for upgrade detection, I found in testing
 * that some requests for .js files were 503'ing and breaking the
 * interceptor.
 */
function isStaticAssetRequest(input) {
  let url;
  try {
    url = typeof input === 'string' ? input : input.url;
  } catch (e) {
    return false;
  }

  // Ignore requests for static assets
  const staticPatterns = [
    '/_next/static/',
    '/_next/image',
    '.js',
    '.mjs',
    '.css',
    '.woff',
    '.woff2',
    '.ttf',
    '.eot',
    '.svg',
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.webp',
    '.ico',
  ];

  return staticPatterns.some((pattern) => url.includes(pattern));
}

/**
 * Wraps fetch to intercept 503 responses and report upgrade status
 */
export function createUpgradeAwareFetch(reportUpgrade, clearUpgrade) {
  const originalFetch = window.fetch;

  return async function (input, init) {
    let response;
    try {
      response = await originalFetch(input, init);
    } catch (error) {
      // Network errors (e.g. connection refused) on non-static requests
      // likely indicate the server is down during an upgrade, especially
      // with the default Recreate deployment strategy where there is a gap
      // between the old pod dying and the new pod starting.
      try {
        if (!isStaticAssetRequest(input)) {
          reportUpgrade();
        }
      } catch (e) {
        console.error('Error in upgrade detection interceptor:', e);
      }
      throw error;
    }

    // Wrap upgrade detection in try-catch to never break the original request
    try {
      // Ignore static asset requests
      if (isStaticAssetRequest(input)) {
        return response;
      }

      // Check if this is a 503 response indicating server upgrade.
      // This can come from the SkyPilot API server's
      // GracefulShutdownMiddleware (JSON with detail message) or from
      // the NGINX ingress controller when no backends are available
      // (typically an HTML error page). Both cases indicate the server
      // is unavailable during an upgrade.
      if (response.status === 503) {
        reportUpgrade();
      } else if (
        response.ok ||
        (response.status >= 200 && response.status < 300)
      ) {
        // Clear the upgrade banner when we get a successful response from API
        clearUpgrade();
      }
    } catch (error) {
      // If anything goes wrong with upgrade detection, just log it and continue
      console.error('Error in upgrade detection interceptor:', error);
    }

    return response;
  };
}

/**
 * Install the upgrade-aware fetch interceptor
 */
export function installUpgradeInterceptor(reportUpgrade, clearUpgrade) {
  if (typeof window !== 'undefined') {
    // Check if already installed.
    if (window['__upgradeInterceptorInstalled']) {
      return;
    }
    window.fetch = createUpgradeAwareFetch(reportUpgrade, clearUpgrade);
    window['__upgradeInterceptorInstalled'] = true;
  }
}
