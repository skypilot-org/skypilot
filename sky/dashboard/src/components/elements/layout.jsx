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

function DefaultNavbarLayout({ children, isUpgrading }) {
  return (
    <>
      {/* Fixed top bar with navigation */}
      <div className="fixed top-0 left-0 right-0 z-50 shadow-sm">
        <TopBar />
      </div>

      {/* Upgrade banner */}
      <UpgradeBanner />

      {/* Main content */}
      <div
        className="transition-all duration-200 ease-in-out min-h-screen"
        style={{ paddingTop: isUpgrading ? '112px' : '56px' }}
      >
        <main className="p-6">{children}</main>
      </div>
    </>
  );
}

function LayoutContent({ children, highlighted }) {
  const isMobile = useMobile();
  const { reportUpgrade, clearUpgrade, isUpgrading } = useUpgradeDetection();
  const [pluginsSettled, setPluginsSettled] = useState(false);

  // Install the fetch interceptor on mount
  useEffect(() => {
    installUpgradeInterceptor(reportUpgrade, clearUpgrade);
  }, [reportUpgrade, clearUpgrade]);

  // Wait briefly for navigation plugins to register before showing layout.
  // A navigation plugin (e.g. sidebar) dispatches 'skydashboard:navigation-ready'
  // to cut the wait short. Otherwise we fall back after a timeout.
  useEffect(() => {
    const timer = setTimeout(() => setPluginsSettled(true), 200);
    const handler = () => setPluginsSettled(true);
    window.addEventListener('skydashboard:navigation-ready', handler);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('skydashboard:navigation-ready', handler);
    };
  }, []);

  if (!pluginsSettled) {
    return <div className="min-h-screen bg-gray-50" />;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <PluginSlot
        name="layout.navigation"
        context={{ children, isUpgrading, isMobile }}
        fallback={
          <DefaultNavbarLayout isUpgrading={isUpgrading}>
            {children}
          </DefaultNavbarLayout>
        }
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
