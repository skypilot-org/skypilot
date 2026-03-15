import React, { useEffect, useRef, useState } from 'react';
import Head from 'next/head';
import { useRouter } from 'next/router';
import { CircularProgress } from '@mui/material';
import { usePluginRoute } from '@/plugins/PluginProvider';

/**
 * Catch-all page that handles plugin routes without the /plugins/ prefix.
 * e.g. /dashboard/cron renders the plugin registered at /cron (or /plugins/cron).
 * Specific pages (clusters.js, jobs.js, etc.) take routing priority over this.
 */

function derivePathname(router) {
  const slug = router?.query?.path;
  if (!slug) {
    return null;
  }
  const segments = Array.isArray(slug) ? slug : [slug];
  const filtered = segments.filter(Boolean);
  if (!filtered.length) {
    return null;
  }
  return `/${filtered.join('/')}`;
}

export default function PluginCatchAllPage() {
  const router = useRouter();
  const containerRef = useRef(null);
  const [mountError, setMountError] = useState(null);
  const pathname = derivePathname(router);
  // Try the path directly first (e.g. /cron), then with /plugins/ prefix for
  // backward compatibility with plugins that still register under /plugins/.
  const directRoute = usePluginRoute(pathname);
  const prefixedRoute = usePluginRoute(pathname ? `/plugins${pathname}` : null);
  const route = directRoute || prefixedRoute;

  useEffect(() => {
    const container = containerRef.current;
    if (!route || !container) {
      return undefined;
    }
    setMountError(null);

    let cleanup;
    try {
      cleanup = route.mount({
        container,
        route,
      });
    } catch (error) {
      console.error(
        '[SkyDashboardPlugin] Failed to mount plugin route:',
        route.id,
        error
      );
      setMountError(
        'Failed to render the plugin page. Check the browser console for details.'
      );
    }

    return () => {
      if (typeof cleanup === 'function') {
        try {
          cleanup();
        } catch (error) {
          console.warn(
            '[SkyDashboardPlugin] Error during plugin route cleanup:',
            error
          );
        }
      } else if (route.unmount) {
        try {
          route.unmount({ container, route });
        } catch (error) {
          console.warn(
            '[SkyDashboardPlugin] Error during plugin unmount:',
            error
          );
        }
      }
      if (container) {
        container.innerHTML = '';
      }
    };
  }, [route, pathname]);

  const title = route?.title
    ? `${route.title} | SkyPilot Dashboard`
    : 'Plugin | SkyPilot Dashboard';

  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <div className="min-h-[50vh]">
        {mountError ? (
          <div className="max-w-3xl mx-auto p-6 bg-red-50 text-red-700 rounded-lg border border-red-200">
            {mountError}
          </div>
        ) : (
          <>
            {!route && (
              <div className="flex justify-center items-center h-64">
                <CircularProgress size={20} />
                <span className="ml-2 text-gray-500">
                  {router.isReady
                    ? 'Loading plugin resources...'
                    : 'Preparing plugin route...'}
                </span>
              </div>
            )}
            <div ref={containerRef} />
          </>
        )}
      </div>
    </>
  );
}
