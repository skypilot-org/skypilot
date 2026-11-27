'use client';

// Wait for plugins that need early initialization (e.g., fetch interceptors)
// Such plugins are marked with data-requires-early-init="true" and set
// window.__skyPluginsReady = true when ready
let pluginsReadyPromise = null;

export async function waitForPlugins() {
  if (window.__skyPluginsReady) return;
  if (pluginsReadyPromise) return pluginsReadyPromise;

  // Check if any plugin needs early init
  const needsWait = document.querySelector(
    'script[src*="/plugins/"][data-requires-early-init="true"]'
  );
  if (!needsWait) return;

  // Wait for plugin to signal ready (max 1s)
  pluginsReadyPromise = new Promise((resolve) => {
    const start = Date.now();
    const check = setInterval(() => {
      if (window.__skyPluginsReady || Date.now() - start >= 1000) {
        clearInterval(check);
        resolve();
      }
    }, 50);
  });
  return pluginsReadyPromise;
}
