import React from 'react';
import { TopBar, SidebarProvider } from './sidebar';
import { Footer } from './footer';
import { useMobile } from '@/hooks/useMobile';

function LayoutContent({ children, highlighted }) {
  const isMobile = useMobile();

  return (
    <div className="flex flex-col min-h-screen bg-gray-50">
      {/* Fixed top bar with navigation */}
      <div className="fixed top-0 left-0 right-0 z-50 shadow-sm">
        <TopBar />
      </div>

      {/* Main content wrapper - this will grow */}
      <div
        className="flex-grow transition-all duration-200 ease-in-out"
        style={{ paddingTop: '56px' }}
      >
        <main className="p-6 h-full">{children}</main>
      </div>
      <Footer />
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
