'use client';

/**
 * Wraps fetch to intercept 503 responses and report upgrade status
 */
export function createUpgradeAwareFetch(reportUpgrade, clearUpgrade) {
  const originalFetch = window.fetch;

  return async function (input, init) {
    try {
      const response = await originalFetch(input, init);

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
          // If we can't parse JSON, just report upgrade anyway for 503
          reportUpgrade();
        }
      } else if (response.ok || (response.status >= 200 && response.status < 300)) {
        // Clear the upgrade banner when we get a successful response
        clearUpgrade();
      }

      return response;
    } catch (error) {
      // Network errors or other fetch failures
      throw error;
    }
  };
}

/**
 * Install the upgrade-aware fetch interceptor
 */
export function installUpgradeInterceptor(reportUpgrade, clearUpgrade) {
  if (typeof window !== 'undefined') {
    // Check if already installed (using bracket notation to avoid TypeScript errors)
    if (window['__upgradeInterceptorInstalled']) {
      return;
    }
    window.fetch = createUpgradeAwareFetch(reportUpgrade, clearUpgrade);
    window['__upgradeInterceptorInstalled'] = true;
  }
}

