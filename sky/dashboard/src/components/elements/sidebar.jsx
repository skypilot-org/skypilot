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
  BookDocIcon,
  UsersIcon,
  StarIcon,
  VolumeIcon,
  KueueIcon,
  KeyIcon,
  ShieldIcon,
} from '@/components/elements/icons';
import { Settings, User, Clock, FileCode, PanelLeftClose, PanelLeftOpen } from 'lucide-react';

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
import { useMobile } from '@/hooks/useMobile';
import { UpgradeHint } from '@/components/elements/version-display';
import { useGroupedNavLinks, usePluginRoutes } from '@/plugins/PluginProvider';
import { PluginSlot } from '@/plugins/PluginSlot';

// --- Shared nav item definitions ---
const primaryNavItems = [
  { href: '/clusters', icon: ServerIcon, label: 'Clusters' },
  { href: '/jobs', icon: BriefcaseIcon, label: 'Jobs' },
  { href: '/volumes', icon: VolumeIcon, label: 'Volumes' },
];
const secondaryNavItems = [
  { href: '/recipes', icon: FileCode, label: 'Recipes' },
  { href: '/infra', icon: ChipIcon, label: 'Infra' },
  { href: '/workspaces', icon: BookDocIcon, label: 'Workspaces' },
  { href: '/users', icon: UsersIcon, label: 'Users' },
];
const externalLinks = [
  {
    href: 'https://docs.skypilot.co/en/latest/',
    icon: ExternalLinkIcon,
    label: 'Docs',
  },
  {
    href: 'https://github.com/skypilot-org/skypilot',
    icon: GitHubIcon,
    label: 'GitHub',
  },
  {
    href: 'https://slack.skypilot.co/',
    icon: SlackIcon,
    label: 'Slack',
  },
];

// Create a context for sidebar state management
const SidebarContext = createContext(null);

export function SidebarProvider({ children }) {
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem('sidebar-collapsed') === 'true';
    } catch {
      return false;
    }
  });
  const [userEmail, setUserEmail] = useState(() => {
    try { return localStorage.getItem('sky-user-email'); } catch { return null; }
  });
  const [userRole, setUserRole] = useState(null);

  const toggleMobileSidebar = () => {
    setIsMobileSidebarOpen((prev) => !prev);
  };

  const toggleSidebar = () => {
    setIsSidebarCollapsed((prev) => {
      const next = !prev;
      try { localStorage.setItem('sidebar-collapsed', String(next)); } catch {}
      return next;
    });
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
          try { localStorage.setItem('sky-user-email', data.user.name); } catch {}

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
        } else {
          // No user info — clear cache so stale avatar doesn't linger
          setUserEmail(null);
          try { localStorage.removeItem('sky-user-email'); } catch {}
        }
      })
      .catch((error) => {
        console.error('Error fetching user data:', error);
      });
  }, [fullEndpoint]);

  return (
    <SidebarContext.Provider
      value={{
        isMobileSidebarOpen,
        toggleMobileSidebar,
        isSidebarCollapsed,
        toggleSidebar,
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

export function TopBar() {
  const router = useRouter();
  const isMobile = useMobile();
  const { userEmail, userRole, isMobileSidebarOpen, toggleMobileSidebar, isSidebarCollapsed, toggleSidebar } =
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
        if (isMobileSidebarOpen) {
          toggleMobileSidebar();
        }
      }
      if (
        navDropdownRef.current &&
        !navDropdownRef.current.contains(event.target)
      ) {
        setOpenNavDropdown(null);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [dropdownRef, isMobileSidebarOpen, toggleMobileSidebar]);

  // Function to get user initial
  const getUserInitial = (email) => {
    if (!email) return '?';
    if (email.includes('@')) {
      return email.split('@')[0].charAt(0).toUpperCase();
    }
    return email.charAt(0).toUpperCase();
  };

  // Function to determine if a path is active
  const isActivePath = (path) => {
    if (path === '/workspaces') {
      return (
        router.pathname.startsWith('/workspaces') ||
        router.pathname.startsWith('/workspace')
      );
    }
    return router.pathname.startsWith(path);
  };

  // Sidebar link classes (used for both desktop sidebar and mobile drawer)
  const getSidebarLinkClasses = (path, forceInactive = false) => {
    const isActive = !forceInactive && isActivePath(path);
    return `flex items-center px-3 py-2 text-sm rounded-md transition-colors ${
      isActive
        ? 'bg-blue-50 text-blue-600 font-medium'
        : 'text-gray-600 hover:bg-gray-50 hover:text-blue-600'
    }`;
  };

  const renderPluginIcon = (icon, className) => {
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

  // Render a plugin nav link for the sidebar (desktop + mobile)
  const renderSidebarPluginNavLink = (link, onClick) => {
    const content = (
      <>
        {link.icon && (
          <span className="text-base leading-none mr-3" aria-hidden="true">
            {renderPluginIcon(link.icon, 'w-4 h-4')}
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
          className={getSidebarLinkClasses(link.href, true)}
          onClick={onClick}
        >
          {content}
        </a>
      );
    }

    return (
      <Link
        key={link.id}
        href={resolvePluginHref(link.href)}
        className={getSidebarLinkClasses(link.href)}
        onClick={onClick}
        prefetch={false}
      >
        {content}
      </Link>
    );
  };

  // Render collapsible group for sidebar (desktop + mobile)
  const renderSidebarGroupedPlugins = (groupName, links, onClick) => {
    const isOpen = openNavDropdown === groupName;

    return (
      <div key={groupName} ref={navDropdownRef}>
        <button
          onClick={() => setOpenNavDropdown(isOpen ? null : groupName)}
          className={`flex items-center justify-between w-full px-3 py-2 text-sm rounded-md transition-colors ${
            isOpen
              ? 'text-blue-600 bg-gray-50'
              : 'text-gray-600 hover:bg-gray-50 hover:text-blue-600'
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
          <div className="ml-3 mt-1 space-y-1">
            {links.map((link) => (
              <Link
                key={link.id}
                href={resolvePluginHref(link.href)}
                className="flex items-center px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 hover:text-blue-600 rounded-md transition-colors"
                onClick={() => {
                  setOpenNavDropdown(null);
                  if (onClick) onClick();
                }}
                prefetch={false}
              >
                {link.icon && (
                  <span className="text-base leading-none mr-3">
                    {renderPluginIcon(link.icon, 'w-4 h-4')}
                  </span>
                )}
                <span>{link.label}</span>
              </Link>
            ))}
          </div>
        )}
      </div>
    );
  };

  // Shared nav item renderer — single DOM tree for smooth collapse transitions
  const renderNavItem = (item, onClick, collapsed = false) => {
    const isActive = isActivePath(item.href);
    return (
      <Link
        key={item.href}
        href={item.href}
        className={`flex items-center px-3 py-2 text-sm rounded-md transition-all duration-200 overflow-hidden ${
          isActive
            ? 'bg-blue-50 text-blue-600 font-medium'
            : 'text-gray-600 hover:bg-gray-50 hover:text-blue-600'
        }`}
        onClick={onClick}
        prefetch={false}
        title={collapsed ? item.label : undefined}
      >
        <item.icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-blue-600' : 'text-gray-400'}`} />
        <span className={`ml-3 whitespace-nowrap transition-opacity duration-200 ${collapsed ? 'opacity-0' : 'opacity-100'}`}>
          {item.label}
        </span>
      </Link>
    );
  };

  // User profile dropdown content (shared between desktop and mobile)
  const renderUserDropdownContent = () => {
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
      </>
    );
  };

  // ─── Desktop Sidebar ───────────────────────────────────────────────
  if (!isMobile) {
    const collapsed = isSidebarCollapsed;
    return (
      <aside className={`fixed top-0 left-0 bottom-0 bg-white border-r border-gray-200 z-30 flex flex-col h-screen transition-all duration-200 ease-in-out ${collapsed ? 'w-14' : 'w-40'}`}>
        {/* Header: Logo + Collapse toggle */}
        <div className={`h-14 flex items-center shrink-0 ${collapsed ? 'px-2' : 'px-3'}`}>
          <div className={`overflow-hidden transition-all duration-200 shrink-0 ${collapsed ? 'w-0 opacity-0' : 'w-24 opacity-100'}`}>
            <Link
              href="/"
              className="flex items-center pl-3"
              prefetch={false}
            >
              <div className="h-14 w-20 flex items-center">
                <Image
                  src={`${BASE_PATH}/skypilot.svg`}
                  alt="SkyPilot Logo"
                  width={80}
                  height={48}
                  priority
                  className="w-full h-full object-contain object-left"
                />
              </div>
            </Link>
          </div>
          <button
            onClick={toggleSidebar}
            className={`text-gray-400 hover:text-gray-600 rounded-md transition-colors cursor-pointer shrink-0 ${
              collapsed ? 'px-3 py-2' : 'p-1 ml-auto'
            }`}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {collapsed ? (
              <PanelLeftOpen className="w-4 h-4" />
            ) : (
              <PanelLeftClose className="w-4 h-4" />
            )}
          </button>
        </div>

        {/* Primary nav (scrollable) */}
        <nav className={`flex-1 overflow-y-auto py-2 space-y-1 ${collapsed ? 'px-2' : 'px-3'}`}>
          <div className="px-3 pt-2 pb-1 text-[11px] font-medium text-gray-400 tracking-wider overflow-hidden whitespace-nowrap">
            <span className={`transition-opacity duration-200 ${collapsed ? 'opacity-0' : 'opacity-100'}`}>Workloads</span>
          </div>
          {primaryNavItems.map((item) => renderNavItem(item, undefined, collapsed))}

          <div className="px-3 pt-4 pb-1 text-[11px] font-medium text-gray-400 tracking-wider overflow-hidden whitespace-nowrap">
            <span className={`transition-opacity duration-200 ${collapsed ? 'opacity-0' : 'opacity-100'}`}>Manage</span>
          </div>
          {secondaryNavItems.map((item) => renderNavItem(item, undefined, collapsed))}

          {/* Plugin links */}
          {(ungrouped.length > 0 || Object.keys(groups).length > 0) && (
            <div className="px-3 pt-4 pb-1 text-[11px] font-medium text-gray-400 tracking-wider overflow-hidden whitespace-nowrap">
              <span className={`transition-opacity duration-200 ${collapsed ? 'opacity-0' : 'opacity-100'}`}>Plugins</span>
            </div>
          )}
          {ungrouped.map((link) => renderSidebarPluginNavLink(link))}
          {Object.entries(groups).map(([groupName, links]) =>
            renderSidebarGroupedPlugins(groupName, links)
          )}
        </nav>

        {/* Footer: pinned at bottom */}
        <div className={`mt-auto border-t border-gray-200 py-3 space-y-1 shrink-0 ${collapsed ? 'px-2' : 'px-3'}`}>
          {/* External links */}
          {externalLinks.map((link) => (
            <a
              key={link.href}
              href={link.href}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 hover:text-blue-600 rounded-md transition-all duration-200 overflow-hidden"
              title={collapsed ? link.label : undefined}
            >
              <link.icon className="w-4 h-4 shrink-0 text-gray-400" />
              <span className={`ml-3 whitespace-nowrap transition-opacity duration-200 ${collapsed ? 'opacity-0' : 'opacity-100'}`}>
                {link.label}
              </span>
            </a>
          ))}

          {/* Settings */}
          <Link
            href="/config"
            className={`flex items-center px-3 py-2 text-sm rounded-md transition-all duration-200 overflow-hidden ${
              isActivePath('/config')
                ? 'bg-blue-50 text-blue-600 font-medium'
                : 'text-gray-600 hover:bg-gray-50 hover:text-blue-600'
            }`}
            prefetch={false}
            title={collapsed ? 'Settings' : undefined}
          >
            <Settings className={`w-4 h-4 shrink-0 ${isActivePath('/config') ? 'text-blue-600' : 'text-gray-400'}`} />
            <span className={`ml-3 whitespace-nowrap transition-opacity duration-200 ${collapsed ? 'opacity-0' : 'opacity-100'}`}>
              Settings
            </span>
          </Link>

          {/* Upgrade hint */}
          {!collapsed && <UpgradeHint />}

          {/* User profile */}
          {userEmail && (
            <div className="relative" ref={dropdownRef}>
              <button
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                className="flex items-center w-full px-3 py-2 text-sm rounded-md hover:bg-gray-100 transition-colors duration-200 cursor-pointer overflow-hidden"
                title={collapsed ? (userEmail.includes('@') ? userEmail.split('@')[0] : userEmail) : undefined}
              >
                <div className="w-4 h-4 bg-blue-600 text-white rounded-full flex items-center justify-center text-[11px] font-medium shrink-0 leading-none">
                  {getUserInitial(userEmail)}
                </div>
                <span className={`ml-3 whitespace-nowrap text-gray-700 truncate transition-opacity duration-200 ${collapsed ? 'opacity-0' : 'opacity-100'}`}>
                  Profile
                </span>
              </button>

              {isDropdownOpen && (
                <div className={`absolute bottom-full mb-1 w-48 bg-white rounded-md shadow-lg z-50 border border-gray-200 ${collapsed ? 'left-full ml-1' : 'left-0'}`}>
                  {renderUserDropdownContent()}
                </div>
              )}
            </div>
          )}
        </div>
      </aside>
    );
  }

  // ─── Mobile: slim top bar + hamburger drawer ───────────────────────
  return (
    <>
      <div className="fixed top-0 left-0 right-0 bg-white z-30 h-14 px-4 border-b border-gray-200 shadow-sm">
        <div className="flex items-center justify-between h-full">
          {/* Left side - Hamburger + Logo */}
          <div className="flex items-center space-x-4">
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

            <Link
              href="/"
              className="flex items-center h-full"
              prefetch={false}
            >
              <div className="h-20 w-20 flex items-center justify-center">
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

          {/* Right side - User avatar */}
          {userEmail && (
            <div className="relative" ref={dropdownRef}>
              <button
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                className="inline-flex items-center justify-center rounded-full transition-colors duration-150 cursor-pointer hover:ring-2 hover:ring-blue-200"
                title="User Profile"
              >
                <div className="w-6 h-6 text-xs bg-blue-600 text-white rounded-full flex items-center justify-center font-medium hover:bg-blue-700 transition-colors">
                  {getUserInitial(userEmail)}
                </div>
              </button>

              {isDropdownOpen && (
                <div className="absolute right-0 mt-2 w-48 bg-white rounded-md shadow-lg z-50 border border-gray-200">
                  {renderUserDropdownContent()}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Mobile Navigation Sidebar */}
      {/* Backdrop */}
      {isMobileSidebarOpen && (
        <div
          className="fixed top-14 left-0 right-0 bottom-0 bg-black bg-opacity-50 z-40"
          onClick={toggleMobileSidebar}
        />
      )}

      {/* Mobile Sidebar Drawer */}
      <div
        ref={mobileNavRef}
        className={`fixed top-14 left-0 h-[calc(100vh-56px)] w-64 bg-white border-r border-gray-200 shadow-lg z-50 transform transition-transform duration-300 ease-in-out ${
          isMobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <nav className="flex-1 overflow-y-auto py-6">
          <div className="px-4 space-y-1">
            <div className="px-3 pt-2 pb-1 text-[11px] font-medium text-gray-400 tracking-wider">
              Workloads
            </div>
            {primaryNavItems.map((item) => renderNavItem(item, toggleMobileSidebar))}

            <div className="px-3 pt-4 pb-1 text-[11px] font-medium text-gray-400 tracking-wider">
              Manage
            </div>
            {secondaryNavItems.map((item) => renderNavItem(item, toggleMobileSidebar))}

            {/* Ungrouped plugins */}
            {(ungrouped.length > 0 || Object.keys(groups).length > 0) && (
              <div className="px-3 pt-4 pb-1 text-[11px] font-medium text-gray-400 tracking-wider">
                Plugins
              </div>
            )}
            {ungrouped.map((link) =>
              renderSidebarPluginNavLink(link, toggleMobileSidebar)
            )}

            {/* Grouped plugins */}
            {Object.entries(groups).map(([groupName, links]) =>
              renderSidebarGroupedPlugins(
                groupName,
                links,
                toggleMobileSidebar
              )
            )}

            <div className="border-t border-gray-200 my-3"></div>

            {/* External links */}
            {externalLinks.map((link) => (
              <a
                key={link.href}
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 hover:text-blue-600 rounded-md transition-colors"
                onClick={toggleMobileSidebar}
              >
                <link.icon className="w-5 h-5 mr-3 text-gray-400" />
                {link.label}
              </a>
            ))}

            <Link
              href="/config"
              className={getSidebarLinkClasses('/config')}
              onClick={toggleMobileSidebar}
              prefetch={false}
            >
              <Settings className="w-5 h-5 mr-3" />
              Settings
            </Link>
          </div>
        </nav>
      </div>
    </>
  );
}
