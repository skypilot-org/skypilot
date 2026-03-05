'use client';

import { useEffect, useRef } from 'react';
import { useRouter } from 'next/router';
import {
  initPostHog,
  identifyUser,
  registerDeployment,
  trackPageView,
} from '@/lib/analytics';
import { ENDPOINT } from '@/data/connectors/constants';

/**
 * PostHogProvider initializes analytics on mount, identifies the user,
 * registers deployment metadata, and tracks page views on route changes.
 *
 * Wrap this around your app (inside PluginProvider) so that every page
 * transition is captured.
 */
export default function PostHogProvider({ children }) {
  const router = useRouter();
  const identified = useRef(false);

  // Initialize PostHog once on mount
  useEffect(() => {
    initPostHog();
  }, []);

  // Identify user and register deployment metadata
  useEffect(() => {
    if (identified.current) return;
    identified.current = true;

    const identify = async () => {
      try {
        const res = await fetch(`${ENDPOINT}/users/role`);
        if (!res.ok) return;
        const data = await res.json();
        const userHash = data.user_hash || data.user_id || 'anonymous';
        const username = data.username || data.user || '';
        identifyUser(userHash, username);
      } catch {
        // Ignore – analytics should never break the app
      }

      try {
        const res = await fetch(`${ENDPOINT}/api/health`);
        if (!res.ok) return;
        const data = await res.json();
        registerDeployment({
          sky_version: data.version || 'unknown',
          api_version: data.api_version || 'unknown',
        });
      } catch {
        // Ignore
      }
    };

    identify();
  }, []);

  // Track page views on route changes
  useEffect(() => {
    const handleRouteChange = (url) => {
      trackPageView(url);
    };

    // Track the initial page view
    trackPageView(router.asPath);

    router.events.on('routeChangeComplete', handleRouteChange);
    return () => {
      router.events.off('routeChangeComplete', handleRouteChange);
    };
  }, [router]);

  return children;
}
