'use client';

import React from 'react';
import ReactDOM from 'react-dom/client';
import dynamic from 'next/dynamic';
import PropTypes from 'prop-types';
import '@/app/globals.css';
import { useEffect } from 'react';
import { BASE_PATH } from '@/data/connectors/constants';
import { TourProvider } from '@/hooks/useTour';
import { PluginProvider } from '@/plugins/PluginProvider';

const Layout = dynamic(
  () => import('@/components/elements/layout').then((mod) => mod.Layout),
  { ssr: false }
);

// Expose React and ReactDOM to window for plugins to use
if (typeof window !== 'undefined') {
  window.React = React;
  window.ReactDOM = ReactDOM;
}

function App({ Component, pageProps }) {
  useEffect(() => {
    const link = document.createElement('link');
    link.rel = 'icon';
    link.href = `${BASE_PATH}/favicon.ico`;
    document.head.appendChild(link);
  }, []);

  return (
    <PluginProvider>
      <TourProvider>
        <Layout highlighted={pageProps.highlighted}>
          <Component {...pageProps} />
        </Layout>
      </TourProvider>
    </PluginProvider>
  );
}

App.propTypes = {
  Component: PropTypes.elementType.isRequired,
  pageProps: PropTypes.object.isRequired,
};

export default App;
