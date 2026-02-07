import React, { useEffect } from 'react';
import { TopBar, SidebarProvider, useSidebar } from './sidebar';
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
  const { isSidebarCollapsed } = useSidebar();
  const { reportUpgrade, clearUpgrade, isUpgrading } = useUpgradeDetection();

  // Install the fetch interceptor on mount
  useEffect(() => {
    installUpgradeInterceptor(reportUpgrade, clearUpgrade);
  }, [reportUpgrade, clearUpgrade]);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Sidebar (desktop) or top bar (mobile) */}
      <TopBar />

      {/* Upgrade banner */}
      <UpgradeBanner />

      {/* Main content */}
      <div
        className={`transition-all duration-200 ease-in-out min-h-screen ${isMobile ? '' : (isSidebarCollapsed ? 'ml-14' : 'ml-48')}`}
        style={isMobile ? { paddingTop: isUpgrading ? '112px' : '56px' } : {}}
      >
        <main className="p-6">{children}</main>
      </div>

      {/* Welcome notification for first-time visitors */}
      <WelcomeNotification />

      {/* Badge slot for collapsed sidebar (e.g. trial countdown) */}
      <div id="sidebar-badge-slot-alt" className="fixed bottom-[1.35rem] right-14 z-40" />
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
