import React from 'react';
import { TopBar, SidebarProvider } from './sidebar';
import { useMobile } from '@/hooks/useMobile';
import { WelcomeNotification } from './WelcomeNotification';
import { TourButton } from './TourButton';

function LayoutContent({ children, highlighted }) {
  const isMobile = useMobile();

  return (
    <div className="min-h-screen bg-gray-50">
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

      {/* Welcome notification for first-time visitors */}
      <WelcomeNotification />

      <TourButton />
    </div>
  );
}

export function Layout(props) {
  return (
    <SidebarProvider>
      <LayoutContent {...props} />
    </SidebarProvider>
  );
}
