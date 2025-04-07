import React, { useState, useEffect } from 'react';
import { SideBar, TopBar, SidebarProvider, useSidebar } from './sidebar';

function LayoutContent({ children, highlighted }) {
  const [isMobile, setIsMobile] = useState(false);
  const { isSidebarOpen } = useSidebar();

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };

    // Check on initial load
    checkMobile();

    // Add resize listener
    window.addEventListener('resize', checkMobile);

    // Cleanup
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Fixed top bar */}
      <TopBar />

      {/* Sidebar */}
      <SideBar highlighted={highlighted} />

      {/* Main content */}
      <div
        className={`transition-all duration-200 ease-in-out min-h-screen ${isSidebarOpen ? 'ml-64' : 'ml-0'}`}
        style={{ paddingTop: '56px' }}
      >
        <main className="p-6">{children}</main>
      </div>
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
