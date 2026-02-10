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
      // Network errors or other fetch failures - don't intercept
      throw error;
    }

    // Wrap upgrade detection in try-catch to never break the original request
    try {
      // Ignore static asset requests
      if (isStaticAssetRequest(input)) {
        return response;
      }

      // Check if this is a 503 response indicating server upgrade
      if (response.status === 503) {
        // Try to read the response to check if it's an upgrade message
        const clonedResponse = response.clone();
        try {
          const data = await clonedResponse.json();
          if (
            data.detail &&
            (data.detail.includes('shutting down') ||
              data.detail.includes('try again later'))
          ) {
            reportUpgrade();
          }
        } catch (e) {
          // If we can't parse JSON, it's probably not an API response
          // (could be a script load failure), so ignore it
          console.debug('Non-JSON 503 response, ignoring.');
        }
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
