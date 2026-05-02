import React, { useEffect, useRef, useState } from 'react';
import Head from 'next/head';
import { useRouter } from 'next/router';
import dynamic from 'next/dynamic';
import { CircularProgress } from '@mui/material';
import { usePluginRoute } from '@/plugins/PluginProvider';

// Catch-all so context names containing slashes (e.g. EKS ARNs like
// `arn:aws:eks:.../cluster/<name>`) survive a page refresh — the server
// decodes `%2F` back to `/`, which the previous single-segment dynamic
// route could not match.
//
// Plugin routes registered under `/infra/...` (e.g. `/infra/cloud`,
// `/infra/ssh`) are also served from this page so they don't get hidden
// behind the catch-all.

const GPUs = dynamic(
  () => import('@/components/infra').then((mod) => mod.GPUs),
  { ssr: false }
);

function buildPathname(slug) {
  if (!slug) return null;
  const segments = Array.isArray(slug) ? slug : [slug];
  const filtered = segments.filter(Boolean);
  if (!filtered.length) return null;
  return `/infra/${filtered.join('/')}`;
}

export default function InfraContextPage() {
  const router = useRouter();
  const containerRef = useRef(null);
  const [mountError, setMountError] = useState(null);
  const pathname = router.isReady ? buildPathname(router.query.context) : null;
  const route = usePluginRoute(pathname);

  // Don't fall through to <GPUs /> until plugin scripts have finished
  // loading — otherwise a refresh on `/infra/cloud/<id>` or `/infra/ssh/<id>`
  // briefly mounts the wrong page while the plugin's `registerRoute` call is
  // still in flight. Initialize from the global flag set by PluginProvider
  // when bootstrap finishes; on most navigations the bootstrap already
  // happened during the initial app load, so we render synchronously.
  const [pluginsLoaded, setPluginsLoaded] = useState(
    () =>
      typeof window !== 'undefined' &&
      window.__skyDashboardPluginsLoaded === true
  );
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    if (window.__skyDashboardPluginsLoaded === true) {
      setPluginsLoaded(true);
      return undefined;
    }
    const handler = () => setPluginsLoaded(true);
    window.addEventListener('skydashboard:plugins-loaded', handler);
    // Safety net for the rare case where this page mounts during bootstrap
    // and the event somehow doesn't reach the listener.
    const timer = window.setTimeout(handler, 2000);
    return () => {
      window.removeEventListener('skydashboard:plugins-loaded', handler);
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!route || !container) {
      return undefined;
    }
    setMountError(null);

    let cleanup;
    try {
      cleanup = route.mount({ container, route });
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
    };
  }, [route, pathname]);

  if (route) {
    const title = route.title
      ? `${route.title} | SkyPilot Dashboard`
      : 'Infra | SkyPilot Dashboard';
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
            <div ref={containerRef} />
          )}
        </div>
      </>
    );
  }

  if (!pluginsLoaded) {
    return (
      <>
        <Head>
          <title>Infra | SkyPilot Dashboard</title>
        </Head>
        <div className="flex justify-center items-center h-64">
          <CircularProgress size={20} />
          <span className="ml-2 text-gray-500">Loading…</span>
        </div>
      </>
    );
  }

  return (
    <>
      <Head>
        <title>Infra | SkyPilot Dashboard</title>
      </Head>
      <GPUs />
    </>
  );
}
