import React, { useEffect } from 'react';
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

function LayoutContent({ children, highlighted }) {
  const isMobile = useMobile();
  const { reportUpgrade, clearUpgrade, isUpgrading } = useUpgradeDetection();

  // Install the fetch interceptor on mount
  useEffect(() => {
    installUpgradeInterceptor(reportUpgrade, clearUpgrade);
  }, [reportUpgrade, clearUpgrade]);

  return (
    <div className="min-h-screen bg-gray-50">
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
