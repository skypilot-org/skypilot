import React, {
  useState,
  useRef,
  useEffect,
  createContext,
  useContext,
} from 'react';
import Image from 'next/image';
import { useRouter } from 'next/router';

import Link from 'next/link';
import {
  ServerIcon,
  BriefcaseIcon,
  ServiceBellIcon,
  ExternalLinkIcon,
  GitHubIcon,
  SlackIcon,
  CommentFeedbackIcon,
} from '@/components/elements/icons';
import { BASE_PATH } from '@/data/connectors/constants';
import { CustomTooltip } from '@/components/utils';
import { useMobile } from '@/hooks/useMobile';

// Create a context for sidebar state management
const SidebarContext = createContext(null);

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
  const isMobile = useMobile();
  const sidebarRef = useRef(null);

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
  const router = useRouter();
  const isMobile = useMobile();

  // Function to determine if a path is active
  const isActivePath = (path) => {
    return router.pathname.startsWith(path);
  };

  // Modify the getLinkClasses function to handle mobile styles
  const getLinkClasses = (path) => {
    const isActive = isActivePath(path);
    const baseClasses = isActive
      ? 'border-transparent text-blue-600'
      : 'border-transparent hover:text-blue-600';

    return `inline-flex items-center border-b-2 ${baseClasses} ${
      isMobile ? 'px-2 py-1' : 'px-1 pt-1 space-x-2'
    }`;
  };

  return (
    <div className="fixed top-0 left-0 right-0 bg-white z-30 h-14 px-4 border-b border-gray-200 shadow-sm">
      <div className="flex items-center h-full">
        <div
          className={`flex items-center ${isMobile ? 'space-x-2 mr-2' : 'space-x-4 mr-6'}`}
        >
          <Link
            href="/"
            className="flex items-center px-1 pt-1 h-full"
            prefetch={false}
          >
            <div
              className={`${isMobile ? 'h-16 w-16' : 'h-20 w-20'} flex items-center justify-center`}
            >
              <Image
                src={`${BASE_PATH}/skypilot.svg`}
                alt="SkyPilot Logo"
                width={80}
                height={80}
                priority
                className="w-full h-full object-contain"
              />
            </div>
          </Link>
        </div>

        {/* Navigation links - reduce spacing on mobile */}
        <div
          className={`flex items-center ${isMobile ? 'space-x-1' : 'space-x-2 md:space-x-6'} ${isMobile ? 'mr-2' : 'mr-6'}`}
        >
          <Link
            href="/clusters"
            className={getLinkClasses('/clusters')}
            prefetch={false}
          >
            <ServerIcon className="w-4 h-4" />
            {!isMobile && <span>Clusters</span>}
          </Link>

          <Link
            href="/jobs"
            className={getLinkClasses('/jobs')}
            prefetch={false}
          >
            <BriefcaseIcon className="w-4 h-4" />
            {!isMobile && <span>Jobs</span>}
          </Link>

          <div
            className={`inline-flex items-center ${isMobile ? 'px-2 py-1' : 'px-1 pt-1'} text-gray-400`}
          >
            <ServiceBellIcon className="w-4 h-4" />
            {!isMobile && (
              <>
                <span className="ml-2">Services</span>
                <span className="text-xs ml-2 px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded">
                  Soon
                </span>
              </>
            )}
          </div>
        </div>

        {/* External links - now shows only icons on mobile */}
        <div
          className={`flex items-center space-x-1 ${isMobile ? 'ml-0' : 'ml-auto'}`}
        >
          <CustomTooltip
            content="Documentation"
            className="text-sm text-muted-foreground"
          >
            <a
              href="https://skypilot.readthedocs.io/en/latest/"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center px-2 py-1 text-gray-600 hover:text-blue-600 transition-colors duration-150 cursor-pointer"
              title="Docs"
            >
              {!isMobile && <span className="mr-1">Docs</span>}
              <ExternalLinkIcon
                className={`${isMobile ? 'w-4 h-4' : 'w-3.5 h-3.5'}`}
              />
            </a>
          </CustomTooltip>

          <div className="border-l border-gray-200 h-6 mx-1"></div>

          {/* Keep the rest of the external links as icons only */}
          <CustomTooltip
            content="GitHub Repository"
            className="text-sm text-muted-foreground"
          >
            <a
              href="https://github.com/skypilot-org/skypilot"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center p-2 rounded-full text-gray-600 hover:bg-gray-100 transition-colors duration-150 cursor-pointer"
              title="GitHub"
            >
              <GitHubIcon className={`${isMobile ? 'w-4 h-4' : 'w-5 h-5'}`} />
            </a>
          </CustomTooltip>

          <CustomTooltip
            content="Join Slack"
            className="text-sm text-muted-foreground"
          >
            <a
              href="https://slack.skypilot.co/"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center p-2 rounded-full text-gray-600 hover:bg-gray-100 transition-colors duration-150 cursor-pointer"
              title="Slack"
            >
              <SlackIcon className={`${isMobile ? 'w-4 h-4' : 'w-5 h-5'}`} />
            </a>
          </CustomTooltip>

          <CustomTooltip
            content="Leave Feedback"
            className="text-sm text-muted-foreground"
          >
            <a
              href="https://github.com/skypilot-org/skypilot/issues/new"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center p-2 rounded-full text-gray-600 hover:bg-gray-100 transition-colors duration-150 cursor-pointer"
              title="Leave Feedback"
            >
              <CommentFeedbackIcon
                className={`${isMobile ? 'w-4 h-4' : 'w-5 h-5'}`}
              />
            </a>
          </CustomTooltip>
        </div>
      </div>
    </div>
  );
}
