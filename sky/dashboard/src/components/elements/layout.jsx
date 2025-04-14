import React, { useState, useEffect } from 'react';
import { TopBar, SidebarProvider } from './sidebar';

function LayoutContent({ children, highlighted }) {
  const [isMobile, setIsMobile] = useState(false);

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
