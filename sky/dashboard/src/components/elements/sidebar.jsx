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
  UsersIcon,
  StarIcon,
  VolumeIcon,
  KueueIcon,
  KeyIcon,
  ShieldIcon,
} from '@/components/elements/icons';
import { Settings, User, Clock, FileCode } from 'lucide-react';

// Map icon names to icon components for plugin nav links
const ICON_MAP = {
  key: KeyIcon,
  shield: ShieldIcon,
  server: ServerIcon,
  briefcase: BriefcaseIcon,
  chip: ChipIcon,
  book: BookDocIcon,
  users: UsersIcon,
  volume: VolumeIcon,
  clock: Clock,
  kueue: KueueIcon,
  filecode: FileCode,
};
import { BASE_PATH, ENDPOINT } from '@/data/connectors/constants';
import { CustomTooltip } from '@/components/utils';
import { useMobile } from '@/hooks/useMobile';
import { UpgradeHint } from '@/components/elements/version-display';
import { useGroupedNavLinks, usePluginRoutes } from '@/plugins/PluginProvider';
import { PluginSlot } from '@/plugins/PluginSlot';

// Create a context for sidebar state management
const SidebarContext = createContext(null);

export function SidebarProvider({ children }) {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const [userEmail, setUserEmail] = useState(null);
  const [userRole, setUserRole] = useState(null);

  const toggleSidebar = () => {
    setIsSidebarOpen((prev) => !prev);
  };

  const toggleMobileSidebar = () => {
    setIsMobileSidebarOpen((prev) => !prev);
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
      value={{
        isSidebarOpen,
        toggleSidebar,
        isMobileSidebarOpen,
        toggleMobileSidebar,
        userEmail,
        userRole,
      }}
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
  const { userEmail, userRole, isMobileSidebarOpen, toggleMobileSidebar } =
    useSidebar();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [openNavDropdown, setOpenNavDropdown] = useState(null);
  const { ungrouped, groups } = useGroupedNavLinks();
  const pluginRoutes = usePluginRoutes();

  const dropdownRef = useRef(null);
  const mobileNavRef = useRef(null);
  const navDropdownRef = useRef(null);

  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsDropdownOpen(false);
      }
      if (
        mobileNavRef.current &&
        !mobileNavRef.current.contains(event.target) &&
        !event.target.closest('.mobile-menu-button')
      ) {
        // Close mobile sidebar if clicking outside and not on the hamburger button
        if (isMobileSidebarOpen) {
          toggleMobileSidebar();
        }
      }
      // Handle navigation dropdown menu clicks outside
      if (
        navDropdownRef.current &&
        !navDropdownRef.current.contains(event.target)
      ) {
        setOpenNavDropdown(null);
      }
    }
    // Bind the event listener
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      // Unbind the event listener on clean up
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [dropdownRef, isMobileSidebarOpen, toggleMobileSidebar]);

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

  const getMobileLinkClasses = (path, forceInactive = false) => {
    const isActive = !forceInactive && isActivePath(path);
    return `flex items-center px-4 py-3 text-sm font-medium rounded-md transition-colors ${
      isActive
        ? 'bg-blue-50 text-blue-600'
        : 'text-gray-700 hover:bg-gray-100 hover:text-blue-600'
    }`;
  };

  const renderPluginIcon = (icon, className) => {
    // Handle React elements directly (allows plugins to provide their own icons)
    if (React.isValidElement(icon)) {
      return React.cloneElement(icon, {
        className: [icon.props?.className, className].filter(Boolean).join(' '),
      });
    }
    // Handle string names via ICON_MAP (backward compatible)
    const IconComponent = ICON_MAP[icon];
    if (IconComponent) {
      return React.createElement(IconComponent, { className });
    }
    return icon;
  };

  const renderNavLabel = (link) => (
    <>
      {link.icon && (
        <span className="text-base leading-none mr-1" aria-hidden="true">
          {renderPluginIcon(link.icon, 'w-4 h-4')}
        </span>
      )}
      <span className="inline-flex items-center gap-1">
        <span>{link.label}</span>
        {link.badge && (
          <span className="text-[10px] uppercase tracking-wide bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">
            {link.badge}
          </span>
        )}
      </span>
    </>
  );

  const resolvePluginHref = (href) => {
    if (typeof href !== 'string') {
      return href;
    }
    const route = pluginRoutes.find((entry) => entry.path === href);
    if (!route || !route.path.startsWith('/plugins')) {
      return href;
    }
    const slugSegments = route.path
      .replace(/^\/+/, '')
      .split('/')
      .slice(1)
      .filter(Boolean);
    return {
      pathname: '/plugins/[...slug]',
      query: slugSegments.length ? { slug: slugSegments } : {},
    };
  };

  const renderDesktopPluginNavLink = (link) => {
    if (link.external) {
      return (
        <a
          key={link.id}
          href={link.href}
          target={link.target}
          rel={link.rel}
          className="inline-flex items-center border-b-2 border-transparent px-1 pt-1 space-x-2 text-gray-700 hover:text-blue-600"
        >
          {renderNavLabel(link)}
        </a>
      );
    }

    return (
      <Link
        key={link.id}
        href={resolvePluginHref(link.href)}
        className={getLinkClasses(link.href)}
        prefetch={false}
      >
        {renderNavLabel(link)}
      </Link>
    );
  };

  const renderMobilePluginNavLink = (link) => {
    const content = (
      <>
        {link.icon && (
          <span className="text-base leading-none mr-2" aria-hidden="true">
            {renderPluginIcon(link.icon, 'w-5 h-5')}
          </span>
        )}
        <span className="flex items-center gap-2">
          <span>{link.label}</span>
          {link.badge && (
            <span className="text-[10px] uppercase tracking-wide bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">
              {link.badge}
            </span>
          )}
        </span>
      </>
    );

    if (link.external) {
      return (
        <a
          key={link.id}
          href={link.href}
          target={link.target}
          rel={link.rel}
          className={getMobileLinkClasses(link.href, true)}
          onClick={toggleMobileSidebar}
        >
          {content}
        </a>
      );
    }

    return (
      <Link
        key={link.id}
        href={resolvePluginHref(link.href)}
        className={getMobileLinkClasses(link.href)}
        onClick={toggleMobileSidebar}
        prefetch={false}
      >
        {content}
      </Link>
    );
  };

  // Render desktop dropdown menu for grouped plugins
  const renderDesktopDropdownMenu = (groupName, links) => {
    const isOpen = openNavDropdown === groupName;

    return (
      <div className="relative" key={groupName} ref={navDropdownRef}>
        <button
          onClick={() => setOpenNavDropdown(isOpen ? null : groupName)}
          className={`inline-flex items-center align-middle border-b-2 px-1 pt-1 space-x-1 ${
            isOpen
              ? 'text-blue-600 border-blue-600'
              : 'border-transparent text-gray-700 hover:text-blue-600'
          }`}
        >
          <span>{groupName}</span>
          <svg
            className={`w-4 h-4 transition-transform ${isOpen ? 'rotate-180' : ''}`}
            fill="currentColor"
            viewBox="0 0 20 20"
          >
            <path
              fillRule="evenodd"
              d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
              clipRule="evenodd"
            />
          </svg>
        </button>

        {isOpen && (
          <div className="absolute top-full left-0 mt-1 min-w-[8rem] bg-white rounded-md shadow-lg border border-gray-200 z-50">
            <div className="py-1">
              {links.map((link) => (
                <Link
                  key={link.id}
                  href={resolvePluginHref(link.href)}
                  className="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 transition-colors"
                  onClick={() => setOpenNavDropdown(null)}
                  prefetch={false}
                >
                  <div className="flex items-center gap-2">
                    {link.icon && (
                      <span className="text-base leading-none">
                        {renderPluginIcon(link.icon, 'w-4 h-4')}
                      </span>
                    )}
                    <span>{link.label}</span>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      <div className="fixed top-0 left-0 right-0 bg-white z-30 h-14 px-4 border-b border-gray-200 shadow-sm">
        <div className="flex items-center justify-between h-full">
          {/* Left side - Logo and mobile hamburger */}
          <div className="flex items-center space-x-4 mr-4 md:mr-6">
            {/* Mobile hamburger menu button */}
            {isMobile && (
              <button
                onClick={toggleMobileSidebar}
                className="mobile-menu-button p-2 rounded-md text-gray-600 hover:text-blue-600 hover:bg-gray-100 transition-colors"
                aria-label="Toggle mobile menu"
              >
                <svg
                  className="w-5 h-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d={
                      isMobileSidebarOpen
                        ? 'M6 18L18 6M6 6l12 12'
                        : 'M4 6h16M4 12h16M4 18h16'
                    }
                  />
                </svg>
              </button>
            )}

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

          {/* Desktop Navigation links */}
          {!isMobile && (
            <div className="flex items-center space-x-2 md:space-x-4 mr-6">
              <Link
                href="/clusters"
                className={getLinkClasses('/clusters')}
                prefetch={false}
              >
                <ServerIcon className="w-4 h-4" />
                <span>Clusters</span>
              </Link>

              <Link
                href="/jobs"
                className={getLinkClasses('/jobs')}
                prefetch={false}
              >
                <BriefcaseIcon className="w-4 h-4" />
                <span>Jobs</span>
              </Link>

              <Link
                href="/volumes"
                className={getLinkClasses('/volumes')}
                prefetch={false}
              >
                <VolumeIcon className="w-4 h-4" />
                <span>Volumes</span>
              </Link>

              <div className="border-l border-gray-200 h-6 mx-1"></div>

              <Link
                href="/recipes"
                className={getLinkClasses('/recipes')}
                prefetch={false}
              >
                <FileCode className="w-4 h-4" />
                <span>Recipes</span>
              </Link>

              <Link
                href="/infra"
                className={getLinkClasses('/infra')}
                prefetch={false}
              >
                <ChipIcon className="w-4 h-4" />
                <span>Infra</span>
              </Link>

              <Link
                href="/workspaces"
                className={getLinkClasses('/workspaces')}
                prefetch={false}
              >
                <BookDocIcon className="w-4 h-4" />
                <span>Workspaces</span>
              </Link>

              <Link
                href="/users"
                className={getLinkClasses('/users')}
                prefetch={false}
              >
                <UsersIcon className="w-4 h-4" />
                <span>Users</span>
              </Link>
            </div>
          )}

          {/* External links and user profile - only show on desktop, mobile uses sidebar */}
          <div className="flex items-center space-x-1 ml-auto">
            {!isMobile && (
              <>
                {/* Ungrouped plugin links - positioned on the right */}
                {ungrouped.map((link) => renderDesktopPluginNavLink(link))}

                {/* Grouped dropdown menus (e.g., Enterprise) - positioned on the right */}
                {Object.entries(groups).map(([groupName, links]) =>
                  renderDesktopDropdownMenu(groupName, links)
                )}

                <CustomTooltip
                  content="Documentation"
                  className="text-sm text-muted-foreground"
                >
                  <a
                    href="https://docs.skypilot.co/en/latest/"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center align-middle border-b-2 border-transparent px-1 pt-1 space-x-1 text-gray-600 hover:text-blue-600 transition-colors duration-150 cursor-pointer"
                    title="Docs"
                  >
                    <span className="leading-none">Docs</span>
                    <ExternalLinkIcon className="w-3.5 h-3.5" />
                  </a>
                </CustomTooltip>

                <CustomTooltip
                  content="GitHub Repository"
                  className="text-sm text-muted-foreground"
                >
                  <a
                    href="https://github.com/skypilot-org/skypilot"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center justify-center align-middle p-2 rounded-full text-gray-600 hover:bg-gray-100 transition-colors duration-150 cursor-pointer"
                    title="GitHub"
                  >
                    <GitHubIcon className="w-5 h-5" />
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
                    className="inline-flex items-center justify-center align-middle p-2 rounded-full text-gray-600 hover:bg-gray-100 transition-colors duration-150 cursor-pointer"
                    title="Slack"
                  >
                    <SlackIcon className="w-5 h-5" />
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
                    className="inline-flex items-center justify-center align-middle p-2 rounded-full text-gray-600 hover:bg-gray-100 transition-colors duration-150 cursor-pointer"
                    title="Leave Feedback"
                  >
                    <CommentFeedbackIcon className="w-5 h-5" />
                  </a>
                </CustomTooltip>

                <div className="border-l border-gray-200 h-6"></div>

                {/* Version Display */}
                <UpgradeHint />

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
                    <Settings className="w-5 h-5" />
                  </Link>
                </CustomTooltip>
              </>
            )}

            {/* User Profile Icon and Dropdown */}
            {userEmail && (
              <div className="relative" ref={dropdownRef}>
                <button
                  onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                  className="inline-flex items-center justify-center rounded-full transition-colors duration-150 cursor-pointer hover:ring-2 hover:ring-blue-200"
                  title="User Profile"
                >
                  <div
                    className={`${isMobile ? 'w-6 h-6 text-xs' : 'w-7 h-7 text-sm'} bg-blue-600 text-white rounded-full flex items-center justify-center font-medium hover:bg-blue-700 transition-colors`}
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
                    <PluginSlot
                      name="user-menu"
                      wrapperClassName="contents"
                      context={{
                        closeDropdown: () => setIsDropdownOpen(false),
                      }}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Mobile Navigation Sidebar */}
      {isMobile && (
        <>
          {/* Backdrop */}
          {isMobileSidebarOpen && (
            <div
              className="fixed top-14 left-0 right-0 bottom-0 bg-black bg-opacity-50 z-40"
              onClick={toggleMobileSidebar}
            />
          )}

          {/* Mobile Sidebar */}
          <div
            ref={mobileNavRef}
            className={`fixed top-14 left-0 h-[calc(100vh-56px)] w-64 bg-white border-r border-gray-200 shadow-lg z-50 transform transition-transform duration-300 ease-in-out ${
              isMobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'
            }`}
          >
            <nav className="flex-1 overflow-y-auto py-6">
              <div className="px-4 space-y-1">
                <Link
                  href="/clusters"
                  className={`flex items-center px-4 py-3 text-sm font-medium rounded-md transition-colors ${
                    isActivePath('/clusters')
                      ? 'bg-blue-50 text-blue-600'
                      : 'text-gray-700 hover:bg-gray-100 hover:text-blue-600'
                  }`}
                  onClick={toggleMobileSidebar}
                  prefetch={false}
                >
                  <ServerIcon className="w-5 h-5 mr-3" />
                  Clusters
                </Link>

                <Link
                  href="/jobs"
                  className={`flex items-center px-4 py-3 text-sm font-medium rounded-md transition-colors ${
                    isActivePath('/jobs')
                      ? 'bg-blue-50 text-blue-600'
                      : 'text-gray-700 hover:bg-gray-100 hover:text-blue-600'
                  }`}
                  onClick={toggleMobileSidebar}
                  prefetch={false}
                >
                  <BriefcaseIcon className="w-5 h-5 mr-3" />
                  Jobs
                </Link>

                <Link
                  href="/volumes"
                  className={`flex items-center px-4 py-3 text-sm font-medium rounded-md transition-colors ${
                    isActivePath('/volumes')
                      ? 'bg-blue-50 text-blue-600'
                      : 'text-gray-700 hover:bg-gray-100 hover:text-blue-600'
                  }`}
                  onClick={toggleMobileSidebar}
                  prefetch={false}
                >
                  <VolumeIcon className="w-5 h-5 mr-3" />
                  Volumes
                </Link>

                <div className="border-t border-gray-200 my-4"></div>

                <Link
                  href="/recipes"
                  className={`flex items-center px-4 py-3 text-sm font-medium rounded-md transition-colors ${
                    isActivePath('/recipes')
                      ? 'bg-blue-50 text-blue-600'
                      : 'text-gray-700 hover:bg-gray-100 hover:text-blue-600'
                  }`}
                  onClick={toggleMobileSidebar}
                  prefetch={false}
                >
                  <FileCode className="w-5 h-5 mr-3" />
                  Recipes
                </Link>

                <Link
                  href="/infra"
                  className={`flex items-center px-4 py-3 text-sm font-medium rounded-md transition-colors ${
                    isActivePath('/infra')
                      ? 'bg-blue-50 text-blue-600'
                      : 'text-gray-700 hover:bg-gray-100 hover:text-blue-600'
                  }`}
                  onClick={toggleMobileSidebar}
                  prefetch={false}
                >
                  <ChipIcon className="w-5 h-5 mr-3" />
                  Infra
                </Link>

                <Link
                  href="/workspaces"
                  className={`flex items-center px-4 py-3 text-sm font-medium rounded-md transition-colors ${
                    isActivePath('/workspaces')
                      ? 'bg-blue-50 text-blue-600'
                      : 'text-gray-700 hover:bg-gray-100 hover:text-blue-600'
                  }`}
                  onClick={toggleMobileSidebar}
                  prefetch={false}
                >
                  <BookDocIcon className="w-5 h-5 mr-3" />
                  Workspaces
                </Link>

                <Link
                  href="/users"
                  className={`flex items-center px-4 py-3 text-sm font-medium rounded-md transition-colors ${
                    isActivePath('/users')
                      ? 'bg-blue-50 text-blue-600'
                      : 'text-gray-700 hover:bg-gray-100 hover:text-blue-600'
                  }`}
                  onClick={toggleMobileSidebar}
                  prefetch={false}
                >
                  <UsersIcon className="w-5 h-5 mr-3" />
                  Users
                </Link>

                <div className="border-t border-gray-200 my-4"></div>

                {/* Ungrouped plugins */}
                {ungrouped.map((link) => renderMobilePluginNavLink(link))}

                {/* Grouped plugins (displayed flat on mobile) */}
                {Object.entries(groups).map(([groupName, links]) => (
                  <div key={groupName}>
                    <div className="px-4 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                      {groupName}
                    </div>
                    {links.map((link) => renderMobilePluginNavLink(link))}
                  </div>
                ))}

                {(ungrouped.length > 0 || Object.keys(groups).length > 0) && (
                  <div className="border-t border-gray-200 my-4"></div>
                )}

                {/* External links in mobile */}
                <a
                  href="https://docs.skypilot.co/en/latest/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-100 hover:text-blue-600 rounded-md transition-colors"
                  onClick={toggleMobileSidebar}
                >
                  <ExternalLinkIcon className="w-5 h-5 mr-3" />
                  Documentation
                </a>

                <a
                  href="https://github.com/skypilot-org/skypilot"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-100 hover:text-blue-600 rounded-md transition-colors"
                  onClick={toggleMobileSidebar}
                >
                  <GitHubIcon className="w-5 h-5 mr-3" />
                  GitHub
                </a>

                <a
                  href="https://slack.skypilot.co/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-100 hover:text-blue-600 rounded-md transition-colors"
                  onClick={toggleMobileSidebar}
                >
                  <SlackIcon className="w-5 h-5 mr-3" />
                  Slack
                </a>

                <Link
                  href="/config"
                  className={`flex items-center px-4 py-3 text-sm font-medium rounded-md transition-colors ${
                    isActivePath('/config')
                      ? 'bg-blue-50 text-blue-600'
                      : 'text-gray-700 hover:bg-gray-100 hover:text-blue-600'
                  }`}
                  onClick={toggleMobileSidebar}
                  prefetch={false}
                >
                  <Settings className="w-5 h-5 mr-3" />
                  Configuration
                </Link>
              </div>
            </nav>
          </div>
        </>
      )}
    </>
  );
}
