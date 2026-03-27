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

function LayoutContent({ children, highlighted }) {
  const isMobile = useMobile();
  const { reportUpgrade, clearUpgrade } = useUpgradeDetection();
  const [pluginsSettled, setPluginsSettled] = useState(false);

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
    const settle = () => setPluginsSettled(true);
    const timer = setTimeout(settle, 5000);
    window.addEventListener('skydashboard:navigation-ready', settle);
    window.addEventListener('skydashboard:plugins-loaded', settle);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('skydashboard:navigation-ready', settle);
      window.removeEventListener('skydashboard:plugins-loaded', settle);
    };
  }, []);

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
