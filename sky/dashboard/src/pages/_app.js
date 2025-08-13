'use client';

import React, { useEffect, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';
import PropTypes from 'prop-types';
import '@/app/globals.css';
import { BASE_PATH } from '@/data/connectors/constants';
import { TourProvider } from '@/hooks/useTour';
import AuthManager from '@/lib/auth';
import { apiClient } from '@/data/connectors/client';

const Layout = dynamic(
  () => import('@/components/elements/layout').then((mod) => mod.Layout),
  { ssr: false }
);

function App({ Component, pageProps }) {
  // Simple route check to skip guard on login page
  const isLoginRoute = useMemo(() => {
    if (typeof window === 'undefined') return false;
    return window.location.pathname.startsWith(`${BASE_PATH}/login`);
  }, [typeof window !== 'undefined' ? window.location.pathname : null]);

  const [ready, setReady] = useState(isLoginRoute);

  useEffect(() => {
    if (isLoginRoute) {
      setReady(true);
      return;
    }

    // Quick health check to validate token and avoid rendering flicker
    const controller = new AbortController();
    const verify = async () => {
      try {
        const res = await apiClient.get('/api/health');
        if (res.status === 401) {
          AuthManager.logout();
          return;
        }
        setReady(true);
      } catch (_e) {
        // Network failure: allow render to proceed; individual API calls will handle further 401s
        setReady(true);
      }
    };
    verify();

    return () => controller.abort();
  }, [isLoginRoute]);

  // Avoid rendering layout/content until auth ready, to prevent intermediate empty states
  if (!ready) return null;

  // Render bare login page without layout or tour provider to avoid dialogs pre-auth
  if (isLoginRoute) {
    return <Component {...pageProps} />;
  }

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
