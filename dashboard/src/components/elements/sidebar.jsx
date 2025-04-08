import React, {
  useState,
  useRef,
  useEffect,
  createContext,
  useContext,
} from 'react';
import Image from 'next/image';

import Link from 'next/link';
import { ServerIcon, BriefcaseIcon, ServiceBellIcon } from '@/components/elements/icons';
import { MenuIcon } from 'lucide-react';

// Create a context for sidebar state management
const SidebarContext = createContext();

export function SidebarProvider({ children }) {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  const toggleSidebar = () => {
    setIsSidebarOpen((prev) => !prev);
  };

  return (
    <SidebarContext.Provider value={{ isSidebarOpen, toggleSidebar }}>
      {children}
    </SidebarContext.Provider>
  );
}

// Hook to use the sidebar context
export function useSidebar() {
  const context = useContext(SidebarContext);
  if (!context) {
    throw new Error('useSidebar must be used within a SidebarProvider');
  }
  return context;
}

export function SideBar({ highlighted = 'clusters' }) {
  const { isSidebarOpen, toggleSidebar } = useSidebar();
  const [isMobile, setIsMobile] = useState(false);
  const sidebarRef = useRef(null);

  // Listen to window resize to detect mobile devices
  useEffect(() => {
    function handleResize() {
      setIsMobile(window.innerWidth < 768);
    }

    // Check on initial load
    handleResize();

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, []);

  // Common link style
  const linkStyle = (isHighlighted) => `
        flex items-center space-x-2 
        ${isHighlighted ? 'text-blue-600 font-semibold bg-blue-50' : 'text-gray-700'} 
        relative z-10 py-2 px-4 rounded-sm
        hover:bg-gray-100 hover:text-blue-700 transition-colors
        cursor-pointer w-full
    `;

  return (
    <div
      ref={sidebarRef}
      className={`fixed top-14 left-0 flex flex-col w-64 bg-white border-r border-gray-200 h-[calc(100vh-56px)] z-20 transform transition-transform duration-300 ease-in-out ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}
      style={{ pointerEvents: 'auto' }}
    >
      <nav className="flex-1 overflow-y-auto py-2 mt-2">
        <div className="mt-2">
          <div className="px-4 mb-2 text-xs font-medium text-gray-500 uppercase tracking-wider">
            Workloads
          </div>
          <div className="px-2">
            <Link
              href="/clusters"
              className={linkStyle(highlighted === 'clusters')}
              prefetch={false}
            >
              <ServerIcon className="w-5 h-5 min-w-5" />
              <span>Clusters</span>
            </Link>
            <Link
              href="/jobs"
              className={linkStyle(highlighted === 'jobs')}
              prefetch={false}
            >
              <BriefcaseIcon className="w-5 h-5 min-w-5" />
              <span>Jobs</span>
            </Link>
            <div
              className={`flex items-center space-x-2 text-gray-400 relative z-10 py-2 px-4 rounded-sm w-full`}
            >
              <ServiceBellIcon className="w-5 h-5 min-w-5" />
              <span>Services</span>
              <span className="text-xs ml-2 px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded">
                Soon
              </span>
            </div>
          </div>
        </div>
      </nav>
    </div>
  );
}

export function TopBar() {
  // Use the shared sidebar context
  const { toggleSidebar } = useSidebar();

  // State to track if the viewport is mobile
  const [isMobile, setIsMobile] = useState(false);

  // Effect to handle resize and determine if mobile
  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < 768);
    };

    // Set on mount
    handleResize();

    // Add event listener
    window.addEventListener('resize', handleResize);

    // Cleanup
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, []);

  return (
    <div className="fixed top-0 left-0 right-0 bg-white z-30 h-14 px-4 border-b border-gray-200 shadow-sm">
      <div className="flex items-center justify-between h-full">
        <div className="flex items-center space-x-4">
          <button
            onClick={toggleSidebar}
            className="p-2 rounded-full hover:bg-gray-100"
          >
            <MenuIcon className="w-5 h-5 text-gray-600" />
          </button>

          <Link
            href="/"
            className="flex items-center space-x-2"
            prefetch={false}
          >
            <Image
              src="/skypilot.svg"
              alt="SkyPilot Logo"
              width={32}
              height={32}
              className="h-8 w-8"
            />
            <span className="text-xl font-medium text-sky-blue">SkyPilot</span>
          </Link>
        </div>

        <div className="flex items-center">
          {/* Navigation links */}
          <div className="hidden md:flex items-center space-x-6 mr-6">
            <a
              href="https://skypilot.readthedocs.io/en/latest/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-gray-600 hover:text-blue-600 font-medium"
            >
              Docs
            </a>
            <a
              href="https://blog.skypilot.co/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-gray-600 hover:text-blue-600 font-medium"
            >
              Blog
            </a>
            <a
              href="https://github.com/skypilot-org/skypilot"
              target="_blank"
              rel="noopener noreferrer"
              className="text-gray-600 hover:text-blue-600 font-medium"
            >
              GitHub
            </a>
            <a
              href="https://join.slack.com/t/skypilot-org/shared_invite/zt-1ly8cxp65-Swjn3LKFkNE5CLuH5OjRbQ"
              target="_blank"
              rel="noopener noreferrer"
              className="text-gray-600 hover:text-blue-600 font-medium"
            >
              Slack
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

export function TopNav({ highlighted = 'clusters' }) {
  return (
    <div className="fixed top-14 left-0 right-0 bg-white border-b border-gray-200 z-20">
      <div className="px-4">
        <div className="flex h-12">
          <div className="flex space-x-6">
            <Link
              href="/clusters"
              className={`inline-flex items-center space-x-2 px-1 pt-1 border-b-2 text-sm ${
                highlighted === 'clusters'
                  ? 'border-sky-blue text-sky-blue'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
              prefetch={false}
            >
              <ServerIcon className="w-4 h-4" />
              <span>Clusters</span>
            </Link>
            
            <Link
              href="/jobs"
              className={`inline-flex items-center space-x-2 px-1 pt-1 border-b-2 text-sm ${
                highlighted === 'jobs'
                  ? 'border-sky-blue text-sky-blue'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
              prefetch={false}
            >
              <BriefcaseIcon className="w-4 h-4" />
              <span>Jobs</span>
            </Link>

            <div
              className="inline-flex items-center space-x-2 px-1 pt-1 text-gray-400 text-sm"
            >
              <ServiceBellIcon className="w-4 h-4" />
              <span>Services</span>
              <span className="text-xs ml-2 px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded">
                Soon
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
