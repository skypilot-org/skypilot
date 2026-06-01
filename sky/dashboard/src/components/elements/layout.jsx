import React, { useEffect, useState } from 'react';
import { TopBar, SidebarProvider } from './sidebar';
import { useMobile } from '@/hooks/useMobile';
import { WelcomeNotification } from './WelcomeNotification';
import { TourButton } from './TourButton';
import { UpgradeBanner } from './UpgradeBanner';
import {
  useUpgradeDetection,
  UpgradeDetectionProvider,
} from '@/hooks/useUpgradeDetection';
import { installUpgradeInterceptor } from '@/utils/apiInterceptor';
import { PluginSlot } from '@/plugins/PluginSlot';
import {
  EVENT_NAVIGATION_READY,
  EVENT_PLUGINS_LOADED,
} from '@/data/connectors/constants';

function DefaultNavbarLayout({ children }) {
  return (
    <>
      {/* Fixed top bar with navigation */}
      <div className="fixed top-0 left-0 right-0 z-50 shadow-sm">
        <TopBar />
      </div>

      {/* Main content */}
      <div
        className="transition-all duration-200 ease-in-out min-h-screen"
        style={{ paddingTop: '56px' }}
      >
        <main className="p-6">{children}</main>
      </div>
    </>
  );
}

// Once plugins have settled in any LayoutContent instance, remember it on the
// window so a remount (e.g. when a plugin registers an app-level wrapper into
// PluginWrapperSlot, restructuring the tree) skips the gate immediately
// instead of flashing bg-gray-50 for another settle cycle.
const PLUGINS_SETTLED_FLAG = '__skydashboardPluginsSettled';

function LayoutContent({ children, highlighted }) {
  const isMobile = useMobile();
  const { reportUpgrade, clearUpgrade } = useUpgradeDetection();
  const [pluginsSettled, setPluginsSettled] = useState(
    () => typeof window !== 'undefined' && window[PLUGINS_SETTLED_FLAG] === true
  );

  // Install the fetch interceptor on mount
  useEffect(() => {
    installUpgradeInterceptor(reportUpgrade, clearUpgrade);
  }, [reportUpgrade, clearUpgrade]);

  // Wait for navigation plugins to register before showing layout.
  // A navigation plugin (e.g. sidebar) dispatches 'skydashboard:navigation-ready'
  // to cut the wait short. Otherwise we wait until all plugin scripts have
  // finished loading ('skydashboard:plugins-loaded') so the sidebar plugin has
  // a chance to register before falling back to the default top bar.
  // A safety timeout prevents blocking indefinitely if plugin loading hangs.
  useEffect(() => {
    if (pluginsSettled) return undefined;
    const settle = () => {
      if (typeof window !== 'undefined') {
        window[PLUGINS_SETTLED_FLAG] = true;
      }
      setPluginsSettled(true);
    };
    const timer = setTimeout(settle, 1000);
    const handler = () => {
      clearTimeout(timer);
      settle();
    };
    window.addEventListener(EVENT_NAVIGATION_READY, handler, { once: true });
    window.addEventListener(EVENT_PLUGINS_LOADED, handler, { once: true });
    return () => {
      clearTimeout(timer);
      window.removeEventListener(EVENT_NAVIGATION_READY, handler);
      window.removeEventListener(EVENT_PLUGINS_LOADED, handler);
    };
  }, [pluginsSettled]);

  if (!pluginsSettled) {
    return <div className="min-h-screen bg-gray-50" />;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Upgrade banner - rendered outside PluginSlot so it shows
          regardless of which navigation plugin is active */}
      <UpgradeBanner />

      <PluginSlot
        name="layout.navigation"
        context={{ children, isMobile }}
        fallback={<DefaultNavbarLayout>{children}</DefaultNavbarLayout>}
      />

      {/* Welcome notification for first-time visitors */}
      <WelcomeNotification />

      <TourButton />
    </div>
  );
}

export function Layout(props) {
  return (
    <UpgradeDetectionProvider>
      <SidebarProvider>
        <LayoutContent {...props} />
      </SidebarProvider>
    </UpgradeDetectionProvider>
  );
}
