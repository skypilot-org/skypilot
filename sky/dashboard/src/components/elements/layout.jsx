import React, { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import { TopBar, SidebarProvider } from './sidebar';
import { WelcomeNotification } from './WelcomeNotification';
import { TourButton } from './TourButton';
import { apiClient } from '@/data/connectors/client';

function LayoutContent({ children }) {
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    let canceled = false;
    const check = async () => {
      try {
        const res = await apiClient.get('/api/health');
        if (!canceled) setAuthed(!!(res && res.ok));
      } catch (_) {
        if (!canceled) setAuthed(false);
      }
    };
    check();
    return () => {
      canceled = true;
    };
  }, []);

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
      <WelcomeNotification isAuthenticated={authed} />

      <TourButton />
    </div>
  );
}

LayoutContent.propTypes = {
  children: PropTypes.node,
};

export function Layout(props) {
  return (
    <SidebarProvider>
      <LayoutContent {...props} />
    </SidebarProvider>
  );
}
