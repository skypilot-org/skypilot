'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import PropTypes from 'prop-types';
import '@/app/globals.css';
import { useEffect } from 'react';
import { BASE_PATH } from '@/data/connectors/constants';
import { TourProvider } from '@/hooks/useTour';

const Layout = dynamic(
  () => import('@/components/elements/layout').then((mod) => mod.Layout),
  { ssr: false }
);

function App({ Component, pageProps }) {
  useEffect(() => {
    const link = document.createElement('link');
    link.rel = 'icon';
    link.href = `${BASE_PATH}/favicon.ico`;
    document.head.appendChild(link);
  }, []);

  return (
    <TourProvider>
      <Layout highlighted={pageProps.highlighted}>
        <Component {...pageProps} />
      </Layout>
    </TourProvider>
  );
}

App.propTypes = {
  Component: PropTypes.elementType.isRequired,
  pageProps: PropTypes.object.isRequired,
};

export default App;
