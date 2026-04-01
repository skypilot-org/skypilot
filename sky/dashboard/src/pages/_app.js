'use client';

import React from 'react';
import ReactDOM from 'react-dom/client';
import * as ReactDOMAll from 'react-dom';
import createCache from '@emotion/cache';
import { CacheProvider } from '@emotion/react';
import dynamic from 'next/dynamic';
import PropTypes from 'prop-types';
import '@/app/globals.css';
import { useEffect } from 'react';
import { BASE_PATH } from '@/data/connectors/constants';
import { TourProvider } from '@/hooks/useTour';
import { PluginProvider } from '@/plugins/PluginProvider';
import { VersionProvider } from '@/components/elements/version-display';
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

function App({ Component, pageProps }) {
  useEffect(() => {
    const link = document.createElement('link');
    link.rel = 'icon';
    link.href = `${BASE_PATH}/favicon.ico`;
    document.head.appendChild(link);
  }, []);

  return (
    <CacheProvider value={emotionCache}>
      <PluginProvider>
        <VersionProvider>
          <TourProvider>
            <Layout highlighted={pageProps.highlighted}>
              <Component {...pageProps} />
            </Layout>
          </TourProvider>
        </VersionProvider>
      </PluginProvider>
    </CacheProvider>
  );
}

App.propTypes = {
  Component: PropTypes.elementType.isRequired,
  pageProps: PropTypes.object.isRequired,
};

export default App;
