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
  ChipIcon,
  ServerIcon,
  BriefcaseIcon,
  ExternalLinkIcon,
  GitHubIcon,
  SlackIcon,
  CommentFeedbackIcon,
  BookDocIcon,
  UserCircleIcon,
  UsersIcon,
  StarIcon,
} from '@/components/elements/icons';
import { Settings, User } from 'lucide-react';
import { BASE_PATH, ENDPOINT } from '@/data/connectors/constants';
import { CustomTooltip } from '@/components/utils';
import { useMobile } from '@/hooks/useMobile';

// Create a context for sidebar state management
const SidebarContext = createContext(null);

export function SidebarProvider({ children }) {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [userEmail, setUserEmail] = useState(null);
  const [userRole, setUserRole] = useState(null);

  const toggleSidebar = () => {
    setIsSidebarOpen((prev) => !prev);
  };

  const baseUrl = window.location.origin;
  const fullEndpoint = `${baseUrl}${ENDPOINT}`;
  useEffect(() => {
    // Fetch user info from health endpoint
    fetch(`${fullEndpoint}/api/health`)
      .then((res) => res.json())
      .then((data) => {
        if (data.user && data.user.name) {
          setUserEmail(data.user.name);

          // Get role from direct API endpoint to avoid cache interference
          // Using cache would cause race condition, which leads to unexpected
          // behavior in workspaces and users page.
          const getUserRole = async () => {
            try {
              const response = await fetch(`${fullEndpoint}/users/role`);
              if (response.ok) {
                const roleData = await response.json();
                if (roleData.role) {
                  setUserRole(roleData.role);
                }
              }
            } catch (error) {
              // If role data is not available or there's an error,
              // we just don't show the role - it's not critical
              console.log('Could not fetch user role:', error);
            }
          };

          getUserRole();
        }
      })
      .catch((error) => {
        console.error('Error fetching user data:', error);
      });
  }, [fullEndpoint]);

  return (
    <SidebarContext.Provider
      value={{ isSidebarOpen, toggleSidebar, userEmail, userRole }}
    >
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
          </div>
        </div>
      </nav>
    </div>
  );
}

export function TopBar() {
  const router = useRouter();
  const isMobile = useMobile();
  const { userEmail, userRole } = useSidebar();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const dropdownRef = useRef(null);

  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsDropdownOpen(false);
      }
    }
    // Bind the event listener
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      // Unbind the event listener on clean up
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [dropdownRef]);

  // Function to get user initial
  const getUserInitial = (email) => {
    if (!email) return '?';

    // If it's an email, get the first letter of the username part
    if (email.includes('@')) {
      return email.split('@')[0].charAt(0).toUpperCase();
    }

    // If it's just a name, get the first letter
    return email.charAt(0).toUpperCase();
  };

  // Function to determine if a path is active
  const isActivePath = (path) => {
    // Special case: highlight workspaces for both /workspaces and /workspace paths
    if (path === '/workspaces') {
      return (
        router.pathname.startsWith('/workspaces') ||
        router.pathname.startsWith('/workspace')
      );
    }
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
            <div className={`h-20 w-20 flex items-center justify-center`}>
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
          className={`flex items-center ${isMobile ? 'space-x-1' : 'space-x-2 md:space-x-4'} ${isMobile ? 'mr-2' : 'mr-6'}`}
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

          <div className="border-l border-gray-200 h-6 mx-1"></div>

          <Link
            href="/infra"
            className={getLinkClasses('/infra')}
            prefetch={false}
          >
            <ChipIcon className="w-4 h-4" />
            {!isMobile && <span>Infra</span>}
          </Link>

          {/* Workspaces Link */}
          <Link
            href="/workspaces"
            className={getLinkClasses('/workspaces')}
            prefetch={false}
          >
            <BookDocIcon className="w-4 h-4" />
            {!isMobile && <span>Workspaces</span>}
          </Link>
          <Link
            href="/users"
            className={getLinkClasses('/users')}
            prefetch={false}
          >
            <UsersIcon className="w-4 h-4" />
            {!isMobile && <span>Users</span>}
          </Link>
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

          <div className="border-l border-gray-200 h-6"></div>

          {/* Config Button */}
          <CustomTooltip
            content="Configuration"
            className="text-sm text-muted-foreground"
          >
            <Link
              href="/config"
              className={`inline-flex items-center justify-center p-2 rounded-full transition-colors duration-150 cursor-pointer ${
                isActivePath('/config')
                  ? 'text-blue-600 hover:bg-gray-100'
                  : 'text-gray-600 hover:bg-gray-100'
              }`}
              title="Configuration"
              prefetch={false}
            >
              <Settings className={`${isMobile ? 'w-4 h-4' : 'w-5 h-5'}`} />
            </Link>
          </CustomTooltip>

          {/* User Profile Icon and Dropdown */}
          {userEmail && (
            <div className="relative" ref={dropdownRef}>
              <button
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                className="inline-flex items-center justify-center rounded-full transition-colors duration-150 cursor-pointer hover:ring-2 hover:ring-blue-200"
                title="User Profile"
              >
                <div
                  className={`${isMobile ? 'w-6 h-6' : 'w-7 h-7'} bg-blue-600 text-white rounded-full flex items-center justify-center font-medium ${isMobile ? 'text-xs' : 'text-sm'} hover:bg-blue-700 transition-colors`}
                >
                  {getUserInitial(userEmail)}
                </div>
              </button>

              {isDropdownOpen && (
                <div className="absolute right-0 mt-2 w-48 bg-white rounded-md shadow-lg z-50 border border-gray-200">
                  {(() => {
                    let displayName = userEmail;
                    let emailToDisplay = null;
                    if (userEmail && userEmail.includes('@')) {
                      displayName = userEmail.split('@')[0];
                      emailToDisplay = userEmail;
                    }
                    return (
                      <>
                        <div className="px-4 pt-2 pb-1 text-sm font-medium text-gray-900">
                          {displayName}
                        </div>
                        {emailToDisplay && (
                          <div className="px-4 pt-0 pb-1 text-xs text-gray-500">
                            {emailToDisplay}
                          </div>
                        )}
                        {userRole && (
                          <div className="px-4 pt-0 pb-2 text-xs">
                            {userRole === 'admin' ? (
                              <span className="inline-flex items-center text-blue-600">
                                <StarIcon className="w-3 h-3 mr-1" />
                                Admin
                              </span>
                            ) : (
                              <span className="inline-flex items-center text-gray-600">
                                <User className="w-3 h-3 mr-1" />
                                User
                              </span>
                            )}
                          </div>
                        )}
                      </>
                    );
                  })()}
                  <div className="border-t border-gray-200 mx-1 my-1"></div>
                  <Link
                    href="/users"
                    className="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 hover:text-blue-600"
                    onClick={() => setIsDropdownOpen(false)}
                    prefetch={false}
                  >
                    See all users
                  </Link>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
