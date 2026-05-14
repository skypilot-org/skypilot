import React, { useEffect, useState } from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';
import { CircularProgress } from '@mui/material';
import { EVENT_PLUGINS_LOADED } from '@/data/connectors/constants';

const GPUs = dynamic(
  () => import('@/components/infra').then((mod) => mod.GPUs),
  { ssr: false }
);

export default function InfraPage() {
  // Don't render GPUs until plugins have finished loading, so the list view
  // body doesn't briefly flash through the OSS fallback before the plugin's
  // `registerComponent('infra.page', …)` call lands. Mirror the same pattern
  // used by `pages/infra/[...context].js`.
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
    window.addEventListener(EVENT_PLUGINS_LOADED, handler);
    // Safety net: if the event never fires (no plugins configured, bundle
    // failed to load, network blocked), fall through after 2 seconds so the
    // page is not wedged.
    const timer = window.setTimeout(handler, 2000);
    return () => {
      window.removeEventListener(EVENT_PLUGINS_LOADED, handler);
      window.clearTimeout(timer);
    };
  }, []);

  return (
    <>
      <Head>
        <title>Infra | SkyPilot Dashboard</title>
      </Head>
      {pluginsLoaded ? (
        <GPUs />
      ) : (
        <div className="flex justify-center items-center h-64">
          <CircularProgress size={20} />
          <span className="ml-2 text-gray-500">Loading…</span>
        </div>
      )}
    </>
  );
}
