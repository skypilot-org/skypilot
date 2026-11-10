'use client';

import React from 'react';
import ReactDOM from 'react-dom/client';
import * as ReactDOMAll from 'react-dom';
import createCache from '@emotion/cache';
import { CacheProvider } from '@emotion/react';
import dynamic from 'next/dynamic';
import PropTypes from 'prop-types';
import '@/app/globals.css';
import { useEffect, useState } from 'react';
import { BASE_PATH, EVENT_PLUGINS_LOADED } from '@/data/connectors/constants';
import { TourProvider } from '@/hooks/useTour';
import { PluginProvider } from '@/plugins/PluginProvider';
import { VersionProvider } from '@/components/elements/version-display';
import { PluginWrapperSlot } from '@/plugins/PluginWrapperSlot';
import { getNonce } from '@/utils/csp';

const Layout = dynamic(
  () => import('@/components/elements/layout').then((mod) => mod.Layout),
  { ssr: false }
);

// Expose React and ReactDOM to window for plugins to use.
// We merge react-dom/client (createRoot, hydrateRoot) with react-dom
// (createPortal, flushSync, etc.) so plugins have access to all exports.
if (typeof window !== 'undefined') {
  window.React = React;
  window.ReactDOM = { ...ReactDOMAll, ...ReactDOM };
}

// Create an Emotion cache with the CSP nonce so that dynamically injected
// <style> tags carry the nonce attribute and satisfy the CSP policy.
const nonce = getNonce();
const emotionCache = createCache({ key: 'css', nonce: nonce || undefined });

// Plugin bundles are fetched and registered asynchronously after the app
// mounts, and two of the slots they register into change the *shape* of the
// tree rather than just its contents: PluginWrapperSlot('app.providers')
// inserts a provider level around everything below it, and
// PluginSlot('layout.navigation') swaps its fallback element for the
// registered one. React cannot reconcile a changed element type in place, so
// each of those transitions tears down and remounts the entire subtree —
// including the page and any plugin-mounted React roots inside it. A page
// that fetches on mount therefore fetches once per transition, and flashes
// its loading state each time.
//
// Holding the tree back until plugin registration has settled builds it
// exactly once. The timeout is a backstop for a plugin script that never
// settles; normal loads open the gate on the event.
const PLUGIN_BOOTSTRAP_TIMEOUT_MS = 3000;

function usePluginsSettled() {
  // Starts false on both server and client so the first client render matches
  // the prerendered markup; the effect below opens the gate.
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    if (window.__skyDashboardPluginsLoaded === true) {
      setSettled(true);
      return undefined;
    }
    let timer = null;
    const open = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      setSettled(true);
    };
    timer = setTimeout(open, PLUGIN_BOOTSTRAP_TIMEOUT_MS);
    window.addEventListener(EVENT_PLUGINS_LOADED, open, { once: true });
    return () => {
      if (timer !== null) {
        clearTimeout(timer);
      }
      window.removeEventListener(EVENT_PLUGINS_LOADED, open);
    };
  }, []);

  return settled;
}

function App({ Component, pageProps }) {
  const pluginsSettled = usePluginsSettled();

  useEffect(() => {
    const link = document.createElement('link');
    link.rel = 'icon';
    link.href = `${BASE_PATH}/favicon.ico`;
    document.head.appendChild(link);
  }, []);

  return (
    <CacheProvider value={emotionCache}>
      {/* PluginProvider must render unconditionally — it is what loads the
          plugin bundles the gate below is waiting on. */}
      <PluginProvider>
        {pluginsSettled ? (
          <PluginWrapperSlot name="app.providers">
            <VersionProvider>
              <TourProvider>
                <Layout highlighted={pageProps.highlighted}>
                  <Component {...pageProps} />
                </Layout>
              </TourProvider>
            </VersionProvider>
          </PluginWrapperSlot>
        ) : (
          <div className="min-h-screen bg-gray-50" />
        )}
      </PluginProvider>
    </CacheProvider>
  );
}

App.propTypes = {
  Component: PropTypes.elementType.isRequired,
  pageProps: PropTypes.object.isRequired,
};

export default App;
