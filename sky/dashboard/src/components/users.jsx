'use client';

import React, {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
} from 'react';
import PropTypes from 'prop-types';
import { CircularProgress } from '@mui/material';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { getUsers, getServiceAccountTokens } from '@/data/connectors/users';
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
import dashboardCache from '@/lib/cache';
import cachePreloader from '@/lib/cache-preloader';
import { REFRESH_INTERVALS } from '@/lib/config';
import { sortData } from '@/data/utils';
import {
  CustomTooltip,
  TimestampWithTooltip,
  CustomTooltip as Tooltip,
  LastUpdatedTimestamp,
} from '@/components/utils';
import {
  RotateCwIcon,
  PenIcon,
  CheckIcon,
  XIcon,
  KeyRoundIcon,
  Trash2Icon,
  EyeIcon,
  EyeOffIcon,
  UploadIcon,
  DownloadIcon,
  PlusIcon,
  CopyIcon,
} from 'lucide-react';
import { Layout } from '@/components/elements/layout';
import { useMobile } from '@/hooks/useMobile';
import { useSidebar } from '@/components/elements/sidebar';
import { Card } from '@/components/ui/card';
import { apiClient } from '@/data/connectors/client';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { ErrorDisplay } from '@/components/elements/ErrorDisplay';
import { statusGroups } from '@/components/jobs';
import {
  FilterDropdown,
  Filters,
  updateURLParams as sharedUpdateURLParams,
  updateFiltersByURLParams as sharedUpdateFiltersByURLParams,
  filterData,
} from '@/components/shared/FilterSystem';

const ACTIVE_JOB_STATUSES = new Set(statusGroups.active);

// Define filter options for the filter dropdown
const PROPERTY_OPTIONS = [
  {
    label: 'Name',
    value: 'name',
  },
  {
    label: 'GPU',
    value: 'gpu type', // Match valueList key
  },
  {
    label: 'Infra',
    value: 'infra',
  },
  {
    label: 'User ID',
    value: 'user id', // Match valueList key
  },
  {
    label: 'Role',
    value: 'role',
  },
];

// Helper function to get GPU count with validation
const getGPUCount = (accelerators, source) => {
  if (!accelerators) return 0;

  let parsed = accelerators;

  // Handle string format (from clusters): "{'V100': 4}"
  if (typeof accelerators === 'string') {
    try {
      const jsonStr = accelerators.replace(/'/g, '"').replace(/None/g, 'null');
      parsed = JSON.parse(jsonStr);
    } catch (e) {
      console.error('Failed to parse accelerators string:', accelerators, e);
      return 0;
    }
  }

  // Validate and extract GPU count
  if (typeof parsed === 'object' && parsed !== null) {
    const entries = Object.entries(parsed);

    if (entries.length === 0) {
      return 0;
    }

    if (entries.length > 1) {
      console.warn(
        `${source} has ${entries.length} accelerator entries:`,
        parsed
      );
    }

    // Return the first (and ideally only) GPU count
    return Number(entries[0][1]) || 0;
  }

  return 0;
};

// Helper functions for username parsing
const parseUsername = (username, userId) => {
  if (username && username.includes('@')) {
    return username.split('@')[0];
  }
  // If no email, show username only
  return username || 'N/A';
};

const getFullEmailID = (username, userId) => {
  if (username && username.includes('@')) {
    return username;
  }
  return userId || '-';
};

const REFRESH_INTERVAL = REFRESH_INTERVALS.REFRESH_INTERVAL;

async function checkIsAdmin() {
  try {
    const response = await apiClient.get('/users/role');
    if (!response.ok) return false;
    const data = await response.json();
    return data.role === 'admin';
  } catch {
    return false;
  }
}

// Success display component
const SuccessDisplay = ({ message, onDismiss }) => {
  if (!message) return null;

  return (
    <div className="bg-green-50 border border-green-200 rounded p-4 mb-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center">
          <div className="flex-shrink-0">
            <svg
              className="h-5 w-5 text-green-400"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fillRule="evenodd"
                d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                clipRule="evenodd"
              />
            </svg>
          </div>
          <div className="ml-3">
            <p className="text-sm font-medium text-green-800">{message}</p>
          </div>
        </div>
        {onDismiss && (
          <div className="ml-auto pl-3">
            <div className="-mx-1.5 -my-1.5">
              <button
                type="button"
                onClick={onDismiss}
                className="inline-flex rounded-md bg-green-50 p-1.5 text-green-500 hover:bg-green-100 focus:outline-none focus:ring-2 focus:ring-green-600 focus:ring-offset-2 focus:ring-offset-green-50"
              >
                <span className="sr-only">Dismiss</span>
                <svg
                  className="h-5 w-5"
                  viewBox="0 0 20 20"
                  fill="currentColor"
                >
                  <path
                    fillRule="evenodd"
                    d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                    clipRule="evenodd"
                  />
                </svg>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export function Users() {
  const router = useRouter();
  const { userEmail } = useSidebar();
  const [loading, setLoading] = useState(false);
  const refreshDataRef = useRef(null);
  const isMobile = useMobile();
  const [showCreateUser, setShowCreateUser] = useState(false);
  const [newUser, setNewUser] = useState({
    username: '',
    password: '',
    role: 'user',
  });
  const [creating, setCreating] = useState(false);
  const [permissionDenialState, setPermissionDenialState] = useState({
    open: false,
    message: '',
    userName: '',
  });
  const [userRoleCache, setUserRoleCache] = useState(null);
  const [roleLoading, setRoleLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [showImportExportDialog, setShowImportExportDialog] = useState(false);
  const [csvFile, setCsvFile] = useState(null);
  const [importing, setImporting] = useState(false);
  const [importResults, setImportResults] = useState(null);
  const [activeTab, setActiveTab] = useState('import');
  const [showResetPasswordDialog, setShowResetPasswordDialog] = useState(false);
  const [resetPasswordUser, setResetPasswordUser] = useState(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetLoading, setResetLoading] = useState(false);
  const [resetError, setResetError] = useState(null);
  const [showDeleteConfirmDialog, setShowDeleteConfirmDialog] = useState(false);
  const [userToDelete, setUserToDelete] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [createSuccess, setCreateSuccess] = useState(null);
  const [createError, setCreateError] = useState(null);
  const [basicAuthEnabled, setBasicAuthEnabled] = useState(undefined);
  const [serviceAccountTokenEnabled, setServiceAccountTokenEnabled] =
    useState(undefined);
  const [ingressBasicAuthEnabled, setIngressBasicAuthEnabled] =
    useState(undefined);
  const [externalProxyAuthEnabled, setExternalProxyAuthEnabled] =
    useState(undefined);
  const [healthCheckLoading, setHealthCheckLoading] = useState(true);
  const [activeMainTab, setActiveMainTab] = useState('users');
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showRotateDialog, setShowRotateDialog] = useState(false);
  const [tokenToRotate, setTokenToRotate] = useState(null);
  const [rotating, setRotating] = useState(false);
  const [userSearchQuery, setUserSearchQuery] = useState('');
  const [serviceAccountSearchQuery, setServiceAccountSearchQuery] =
    useState('');
  const [filters, setFilters] = useState([]);
  const [valueList, setValueList] = useState({
    name: [],
    'user id': [],
    role: [],
    'gpu type': [],
    infra: [],
  });
  const [lastFetchedTime, setLastFetchedTime] = useState(null);

  // Initialize deduplicateUsers from URL parameter
  const getInitialDeduplicateUsers = () => {
    if (typeof window !== 'undefined' && router.isReady) {
      const deduplicateParam = router.query.deduplicate;
      // If parameter is explicitly set, use it; otherwise default to true
      if (deduplicateParam !== undefined) {
        return deduplicateParam === 'true';
      }
    }
    return true; // Default to deduplicated view
  };

  const [deduplicateUsers, setDeduplicateUsers] = useState(
    getInitialDeduplicateUsers
  );

  // Sync deduplicateUsers state with URL parameter
  useEffect(() => {
    if (router.isReady) {
      const deduplicateParam = router.query.deduplicate;

      // If URL has no deduplicate parameter, set it to the default
      // Default to false for SSO (userEmail exists), true for non-SSO
      if (deduplicateParam === undefined) {
        const defaultValue = !userEmail; // false for SSO, true for non-SSO
        updateDeduplicateURL(defaultValue);
      } else {
        const expectedState = deduplicateParam === 'true';
        if (deduplicateUsers !== expectedState) {
          setDeduplicateUsers(expectedState);
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.isReady, router.query.deduplicate, userEmail]);

  // Helper function to update deduplicate in URL
  const updateDeduplicateURL = (deduplicateValue) => {
    const query = { ...router.query };
    query.deduplicate = deduplicateValue.toString();

    // Use replace to avoid adding to browser history
    router.replace(
      {
        pathname: router.pathname,
        query,
      },
      undefined,
      { shallow: true }
    );
  };

  // Helper function to update URL query parameters for filters
  const updateURLParams = (filters) => {
    sharedUpdateURLParams(router, filters);
  };

  // Create property map for filter URL parameters
  const propertyMap = new Map([
    ['name', 'Name'],
    ['user id', 'User ID'], // Note: lowercase with space to match URL encoding
    ['role', 'Role'],
    ['gpu type', 'GPU'], // Note: lowercase with space to match URL encoding
    ['infra', 'Infra'],
  ]);

  // Initialize filters from URL parameters
  useEffect(() => {
    if (router.isReady && activeMainTab === 'users') {
      const urlFilters = sharedUpdateFiltersByURLParams(router, propertyMap);
      if (urlFilters.length > 0) {
        setFilters(urlFilters);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.isReady, activeMainTab]);

  // Handle URL parameters for tab selection
  useEffect(() => {
    if (router.isReady) {
      const tab = router.query.tab;
      if (tab === 'service-accounts' && serviceAccountTokenEnabled) {
        setActiveMainTab('service-accounts');
      } else {
        setActiveMainTab('users');
      }
    }
  }, [router.isReady, router.query.tab, serviceAccountTokenEnabled]);

  useEffect(() => {
    async function fetchHealth() {
      setHealthCheckLoading(true);
      try {
        const resp = await apiClient.get('/api/health');
        if (resp.ok) {
          const data = await resp.json();
          setBasicAuthEnabled(!!data.basic_auth_enabled);
          setServiceAccountTokenEnabled(!!data.service_account_token_enabled);
          setIngressBasicAuthEnabled(!!data.ingress_basic_auth_enabled);
          setExternalProxyAuthEnabled(!!data.external_proxy_auth_enabled);
        } else {
          setBasicAuthEnabled(false);
          setServiceAccountTokenEnabled(false);
          setIngressBasicAuthEnabled(false);
          setExternalProxyAuthEnabled(false);
        }
      } catch {
        setBasicAuthEnabled(false);
        setServiceAccountTokenEnabled(false);
        setIngressBasicAuthEnabled(false);
        setExternalProxyAuthEnabled(false);
      } finally {
        setHealthCheckLoading(false);
      }
    }
    fetchHealth();
  }, []);

  const getUserRole = useCallback(async () => {
    if (userRoleCache && Date.now() - userRoleCache.timestamp < 5 * 60 * 1000) {
      return userRoleCache;
    }

    setRoleLoading(true);
    try {
      const response = await apiClient.get(`/users/role`);
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to get user role');
      }
      const data = await response.json();
      const roleData = {
        role: data.role,
        name: data.name,
        id: data.id,
        timestamp: Date.now(),
      };
      setUserRoleCache(roleData);
      setRoleLoading(false);
      return roleData;
    } catch (error) {
      setRoleLoading(false);
      throw error;
    }
  }, [userRoleCache]);

  useEffect(() => {
    getUserRole().catch(() => {
      console.error('Failed to get user role');
    });
  }, [getUserRole]);

  const checkPermissionAndAct = async (action, actionCallback) => {
    try {
      const roleData = await getUserRole();

      if (roleData.role !== 'admin') {
        setPermissionDenialState({
          open: true,
          message: action,
          userName: roleData.name.toLowerCase(),
        });
        return false;
      }

      actionCallback();
      return true;
    } catch (error) {
      console.error('Failed to check user role:', error);
      setPermissionDenialState({
        open: true,
        message: `Error: ${error.message}`,
        userName: '',
      });
      return false;
    }
  };

  const handleRefresh = () => {
    dashboardCache.invalidate(getUsers);
    dashboardCache.invalidate(getClusters);
    dashboardCache.invalidate(getManagedJobs, [
      { allUsers: true, skipFinished: true },
    ]);

    if (refreshDataRef.current) {
      refreshDataRef.current();
    }
  };

  // Effect for keyboard shortcut (Cmd+R / Ctrl+R) to trigger in-page refresh
  useEffect(() => {
    const handleKeyDown = (event) => {
      // Check for Cmd+R (Mac) or Ctrl+R (Windows/Linux)
      if ((event.metaKey || event.ctrlKey) && event.key === 'r') {
        event.preventDefault(); // Prevent browser refresh
        event.stopPropagation(); // Stop event from bubbling
        handleRefresh(); // Trigger our in-page refresh
      }
    };

    // Use capture: true to intercept the event before browser handles it
    document.addEventListener('keydown', handleKeyDown, true);

    return () => {
      document.removeEventListener('keydown', handleKeyDown, true);
    };
  }, []);

  const handleCreateUser = async () => {
    if (!newUser.username || !newUser.password) {
      setCreateError(new Error('Username and password are required.'));
      setShowCreateUser(false);
      return;
    }
    setCreating(true);
    setCreateError(null);
    setCreateSuccess(null);
    try {
      const response = await apiClient.post('/users/create', newUser);
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to create user');
      }
      setCreateSuccess(`User "${newUser.username}" created successfully!`);
      setShowCreateUser(false);
      setNewUser({ username: '', password: '', role: 'user' });
      handleRefresh();
    } catch (error) {
      setCreateError(error);
      setShowCreateUser(false);
      setNewUser({ username: '', password: '', role: 'user' });
    } finally {
      setCreating(false);
    }
  };

  const handleFileUpload = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    setCsvFile(file);
    setImportResults(null);
  };

  const handleImportUsers = async () => {
    if (!csvFile) {
      alert('Please select a CSV file first.');
      return;
    }

    setImporting(true);
    try {
      const reader = new FileReader();
      reader.onload = async (e) => {
        try {
          const csvContent = e.target.result;
          const response = await apiClient.post('/users/import', {
            csv_content: csvContent,
          });

          if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to import users');
          }

          const results = await response.json();

          // Create user-friendly message
          let message = `Import completed. ${results.success_count} users created successfully.`;
          if (results.error_count > 0) {
            message += `\n${results.error_count} failed.`;
            if (results.creation_errors.length > 0) {
              message += `\nErrors: ${results.creation_errors.slice(0, 3).join(', ')}`;
              if (results.creation_errors.length > 3) {
                message += ` and ${results.creation_errors.length - 3} more...`;
              }
            }
          }

          setImportResults({ message });
          if (results.success_count > 0) {
            handleRefresh();
          }
        } catch (error) {
          alert(`Error importing users: ${error.message}`);
        } finally {
          setImporting(false);
        }
      };
      reader.readAsText(csvFile);
    } catch (error) {
      alert(`Error reading file: ${error.message}`);
      setImporting(false);
    }
  };

  const handleResetPasswordClick = async (user) => {
    setResetPasswordUser(user);
    setResetPassword('');
    setShowResetPasswordDialog(true);
  };

  const handleResetPasswordSubmit = async () => {
    if (!resetPassword) {
      setCreateError(new Error('Please enter a new password.'));
      return;
    }
    setResetLoading(true);
    setResetError(null);
    try {
      const response = await apiClient.post('/users/update', {
        user_id: resetPasswordUser.userId,
        password: resetPassword,
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to reset password');
      }
      setCreateSuccess(
        `Password reset successfully for user "${resetPasswordUser.usernameDisplay}"!`
      );
      setShowResetPasswordDialog(false);
      setResetPasswordUser(null);
      setResetPassword('');
    } catch (error) {
      // Show error at top level for better visibility
      setShowResetPasswordDialog(false);
      setResetPasswordUser(null);
      setResetPassword('');
      setResetError(null);
      setCreateError(error);
    } finally {
      setResetLoading(false);
    }
  };

  const handleDeleteUserClick = (user) => {
    checkPermissionAndAct('cannot delete users', () => {
      setUserToDelete(user);
      setShowDeleteConfirmDialog(true);
    });
  };

  const handleDeleteUserConfirm = async () => {
    if (!userToDelete) return;
    setDeleteLoading(true);
    setDeleteError(null);
    try {
      const response = await apiClient.post('/users/delete', {
        user_id: userToDelete.userId,
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to delete user');
      }
      setCreateSuccess(
        `User "${userToDelete.usernameDisplay}" deleted successfully!`
      );
      setShowDeleteConfirmDialog(false);
      setUserToDelete(null);
      handleRefresh();
    } catch (error) {
      // Show error at top level for better visibility
      setShowDeleteConfirmDialog(false);
      setUserToDelete(null);
      setDeleteError(null);
      setCreateError(error);
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleCancelDelete = () => {
    setShowDeleteConfirmDialog(false);
    setUserToDelete(null);
  };

  const handleCancelResetPassword = () => {
    setShowResetPasswordDialog(false);
    setResetPasswordUser(null);
    setResetPassword('');
  };

  // Show loading while fetching health check
  if (healthCheckLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress />
        <span className="ml-2 text-gray-500">Loading...</span>
      </div>
    );
  }

  return (
    <>
      {/* Main Tabs with Controls */}
      <div className="flex items-center justify-between mb-2">
        <div className="text-base flex items-center">
          <button
            className={
              serviceAccountTokenEnabled
                ? `leading-none mr-6 pb-2 px-2 border-b-2 ${
                    activeMainTab === 'users'
                      ? 'text-sky-blue border-sky-500'
                      : 'text-gray-500 hover:text-gray-700 border-transparent'
                  }`
                : 'leading-none mr-6 pb-2 px-2'
            }
            onClick={() => {
              setActiveMainTab('users');
              router.push('/users', undefined, { shallow: true });
            }}
          >
            Users
          </button>
          {serviceAccountTokenEnabled && (
            <button
              className={`leading-none pb-2 px-2 border-b-2 ${
                activeMainTab === 'service-accounts'
                  ? 'text-sky-blue border-sky-500'
                  : 'text-gray-500 hover:text-gray-700 border-transparent'
              }`}
              onClick={() => {
                setActiveMainTab('service-accounts');
                router.push('/users?tab=service-accounts', undefined, {
                  shallow: true,
                });
              }}
            >
              Service Accounts
            </button>
          )}
        </div>

        <div className="flex items-center">
          {loading && (
            <div className="flex items-center mr-2">
              <CircularProgress size={15} className="mt-0" />
              <span className="ml-2 text-gray-500">Loading...</span>
            </div>
          )}
          {activeMainTab === 'users' &&
            basicAuthEnabled &&
            userRoleCache?.role === 'admin' && (
              <button
                onClick={async () => {
                  await checkPermissionAndAct('cannot create users', () => {
                    setShowCreateUser(true);
                  });
                }}
                className="text-sky-blue hover:text-sky-blue-bright flex items-center rounded px-2 py-1 mr-2"
                title="Create New User"
              >
                + New User
              </button>
            )}
          {activeMainTab === 'users' &&
            basicAuthEnabled &&
            userRoleCache?.role === 'admin' && (
              <button
                onClick={async () => {
                  await checkPermissionAndAct('cannot import users', () => {
                    setShowImportExportDialog(true);
                  });
                }}
                className="text-sky-blue hover:text-sky-blue-bright flex items-center rounded px-2 py-1 mr-2"
                title="Import/Export Users"
              >
                <UploadIcon className="h-4 w-4 mr-1" />
                Import/Export
              </button>
            )}

          {!loading && lastFetchedTime && (
            <LastUpdatedTimestamp
              timestamp={lastFetchedTime}
              className="mr-2"
            />
          )}
          <button
            onClick={handleRefresh}
            disabled={loading}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            {!isMobile && <span>Refresh</span>}
          </button>
        </div>
      </div>

      {/* Filter/Search and Create Service Account Row */}
      <div className="flex items-center justify-between mb-4">
        {activeMainTab === 'users' ? (
          <div className="w-full sm:w-auto max-w-md">
            <FilterDropdown
              propertyList={PROPERTY_OPTIONS}
              valueList={valueList}
              setFilters={setFilters}
              updateURLParams={updateURLParams}
              placeholder="Filter users"
            />
          </div>
        ) : (
          <div className="relative flex-1 max-w-md">
            <input
              type="text"
              placeholder="Search by service account name, or created by"
              value={serviceAccountSearchQuery}
              onChange={(e) => {
                setServiceAccountSearchQuery(e.target.value);
              }}
              className="h-8 w-full px-3 pr-8 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-sky-500 focus:border-sky-500 outline-none"
            />
            {serviceAccountSearchQuery && (
              <button
                onClick={() => {
                  setServiceAccountSearchQuery('');
                }}
                className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
                title="Clear search"
              >
                <svg
                  className="h-4 w-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
            )}
          </div>
        )}

        {/* Deduplicate Users Toggle - only show on users tab when NOT using SSO/OAuth2 */}
        {activeMainTab === 'users' && !userEmail && (
          <label className="flex items-center cursor-pointer ml-4">
            <input
              type="checkbox"
              checked={deduplicateUsers}
              onChange={(e) => {
                const newValue = e.target.checked;
                setDeduplicateUsers(newValue);
                updateDeduplicateURL(newValue);
              }}
              className="sr-only"
            />
            <div
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                deduplicateUsers ? 'bg-sky-600' : 'bg-gray-300'
              }`}
            >
              <span
                className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                  deduplicateUsers ? 'translate-x-5' : 'translate-x-1'
                }`}
              />
            </div>
            <span className="ml-2 text-sm text-gray-700">
              Deduplicate users
            </span>
          </label>
        )}

        {/* Create Service Account Button for Service Accounts Tab */}
        {activeMainTab === 'service-accounts' && serviceAccountTokenEnabled && (
          <button
            onClick={() => {
              checkPermissionAndAct(
                'cannot create service account tokens',
                () => {
                  setShowCreateDialog(true);
                }
              );
            }}
            className="ml-4 bg-sky-600 hover:bg-sky-700 text-white flex items-center rounded-md px-3 py-1 text-sm font-medium transition-colors duration-200"
            title="Create Service Account"
          >
            <PlusIcon className="h-4 w-4 mr-2" />
            Create Service Account
          </button>
        )}
      </div>

      {/* Display Active Filters - only for users tab */}
      {activeMainTab === 'users' && (
        <Filters
          filters={filters}
          setFilters={setFilters}
          updateURLParams={updateURLParams}
        />
      )}

      {/* Error/Success messages positioned at top right, below navigation bar */}
      <div className="fixed top-20 right-4 z-[9999] max-w-md">
        <SuccessDisplay
          message={createSuccess}
          onDismiss={() => setCreateSuccess(null)}
        />
        <ErrorDisplay
          error={createError}
          title="Error"
          onDismiss={() => setCreateError(null)}
        />
      </div>

      {activeMainTab === 'users' ? (
        <UsersTable
          refreshInterval={REFRESH_INTERVAL}
          setLoading={setLoading}
          refreshDataRef={refreshDataRef}
          checkPermissionAndAct={checkPermissionAndAct}
          roleLoading={roleLoading}
          onResetPassword={handleResetPasswordClick}
          onDeleteUser={handleDeleteUserClick}
          basicAuthEnabled={basicAuthEnabled}
          ingressBasicAuthEnabled={ingressBasicAuthEnabled}
          externalProxyAuthEnabled={externalProxyAuthEnabled}
          currentUserRole={userRoleCache?.role}
          currentUserId={userRoleCache?.id}
          filters={filters}
          setValueList={setValueList}
          deduplicateUsers={deduplicateUsers}
          setLastFetchedTime={setLastFetchedTime}
        />
      ) : (
        serviceAccountTokenEnabled && (
          <ServiceAccountTokensView
            checkPermissionAndAct={checkPermissionAndAct}
            userRoleCache={userRoleCache}
            setCreateSuccess={setCreateSuccess}
            setCreateError={setCreateError}
            showCreateDialog={showCreateDialog}
            setShowCreateDialog={setShowCreateDialog}
            showRotateDialog={showRotateDialog}
            setShowRotateDialog={setShowRotateDialog}
            tokenToRotate={tokenToRotate}
            setTokenToRotate={setTokenToRotate}
            rotating={rotating}
            setRotating={setRotating}
            searchQuery={serviceAccountSearchQuery}
            setSearchQuery={setServiceAccountSearchQuery}
          />
        )
      )}

      {/* Create User Dialog */}
      <Dialog
        open={showCreateUser}
        onOpenChange={(open) => {
          setShowCreateUser(open);
          if (!open) {
            setCreateError(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Create User</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-4">
            <div className="grid gap-2">
              <label className="text-sm font-medium text-gray-700">
                Username
              </label>
              <input
                className="border rounded px-3 py-2 w-full"
                placeholder="Username"
                value={newUser.username}
                onChange={(e) =>
                  setNewUser({ ...newUser, username: e.target.value })
                }
              />
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium text-gray-700">
                Password
              </label>
              <div className="relative">
                <input
                  className="border rounded px-3 py-2 w-full pr-10"
                  placeholder="Password"
                  type={showPassword ? 'text' : 'password'}
                  value={newUser.password}
                  onChange={(e) =>
                    setNewUser({ ...newUser, password: e.target.value })
                  }
                />
                <button
                  type="button"
                  className="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? (
                    <EyeOffIcon className="h-4 w-4" />
                  ) : (
                    <EyeIcon className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium text-gray-700">Role</label>
              <select
                className="border rounded px-3 py-2 w-full"
                value={newUser.role}
                onChange={(e) =>
                  setNewUser({ ...newUser, role: e.target.value })
                }
              >
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </select>
            </div>
          </div>
          <DialogFooter>
            <button
              className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-10 px-4 py-2"
              onClick={() => setShowCreateUser(false)}
              disabled={creating}
            >
              Cancel
            </button>
            <button
              className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-sky-600 text-white hover:bg-sky-700 h-10 px-4 py-2"
              onClick={handleCreateUser}
              disabled={creating}
            >
              {creating ? 'Creating...' : 'Create'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={permissionDenialState.open}
        onOpenChange={(open) => {
          setPermissionDenialState((prev) => ({ ...prev, open }));
          if (!open) {
            setCreateError(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md transition-all duration-200 ease-in-out">
          <DialogHeader>
            <DialogTitle>Permission Denied</DialogTitle>
            <DialogDescription>
              {roleLoading ? (
                <div className="flex items-center py-2">
                  <CircularProgress size={16} className="mr-2" />
                  <span>Checking permissions...</span>
                </div>
              ) : (
                <>
                  {permissionDenialState.userName ? (
                    <>
                      {permissionDenialState.userName} is logged in as non-admin
                      and {permissionDenialState.message}.
                    </>
                  ) : (
                    permissionDenialState.message
                  )}
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() =>
                setPermissionDenialState((prev) => ({ ...prev, open: false }))
              }
              disabled={roleLoading}
            >
              OK
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Import/Export Users Dialog */}
      <Dialog
        open={showImportExportDialog}
        onOpenChange={(open) => {
          setShowImportExportDialog(open);
          if (!open) {
            setCreateError(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Import/Export Users</DialogTitle>
          </DialogHeader>

          {/* Tabs */}
          <div className="flex border-b border-gray-200 mb-4">
            <button
              className={`px-4 py-2 text-sm font-medium ${
                activeTab === 'import'
                  ? 'border-b-2 border-sky-500 text-sky-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
              onClick={() => setActiveTab('import')}
            >
              Import
            </button>
            <button
              className={`px-4 py-2 text-sm font-medium ${
                activeTab === 'export'
                  ? 'border-b-2 border-sky-500 text-sky-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
              onClick={() => setActiveTab('export')}
            >
              Export
            </button>
          </div>

          <div className="flex flex-col gap-4 py-4">
            {activeTab === 'import' ? (
              <>
                <div className="grid gap-2">
                  <label className="text-sm font-medium text-gray-700">
                    CSV File
                  </label>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={handleFileUpload}
                    className="border rounded px-3 py-2 w-full"
                  />
                  <p className="text-xs text-gray-500">
                    CSV should have columns: username, password, role
                    <br />
                    Supports both plain text passwords and exported password
                    hashes.
                  </p>
                </div>

                {importResults && (
                  <div className="p-3 bg-green-50 border border-green-200 rounded text-green-700 text-sm">
                    {importResults.message}
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="grid gap-2">
                  <label className="text-sm font-medium text-gray-700">
                    Export Users to CSV
                  </label>
                  <p className="text-xs text-gray-500">
                    Download all users as a CSV file with password hashes.
                  </p>
                  <div className="p-3 bg-amber-50 border border-amber-200 rounded">
                    <p className="text-sm text-amber-700">
                      ⚠️ This will export all users with columns: username,
                      password (hashed), role
                    </p>
                    <p className="text-xs text-amber-600 mt-1">
                      Password hashes can be imported directly for system
                      backups.
                    </p>
                  </div>
                </div>
              </>
            )}
          </div>

          <DialogFooter>
            <button
              className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-10 px-4 py-2"
              onClick={() => setShowImportExportDialog(false)}
              disabled={importing}
            >
              Cancel
            </button>
            {activeTab === 'import' ? (
              <button
                className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-sky-600 text-white hover:bg-sky-700 h-10 px-4 py-2"
                onClick={handleImportUsers}
                disabled={importing || !csvFile}
              >
                {importing ? 'Importing...' : 'Import'}
              </button>
            ) : (
              <button
                className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-sky-600 text-white hover:bg-sky-700 h-10 px-4 py-2"
                onClick={async () => {
                  try {
                    const response = await apiClient.get('/users/export');
                    if (!response.ok) {
                      const errorData = await response.json();
                      throw new Error(
                        errorData.detail || 'Failed to export users'
                      );
                    }

                    const data = await response.json();
                    const csvContent = data.csv_content;

                    // Download the CSV file
                    const blob = new Blob([csvContent], {
                      type: 'text/csv;charset=utf-8;',
                    });
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = url;
                    const now = new Date();
                    const pad = (n) => String(n).padStart(2, '0');
                    const y = now.getFullYear();
                    const m = pad(now.getMonth() + 1);
                    const d = pad(now.getDate());
                    const h = pad(now.getHours());
                    const min = pad(now.getMinutes());
                    const s = pad(now.getSeconds());
                    link.download = `users_export_${y}-${m}-${d}-${h}-${min}-${s}.csv`;
                    link.click();
                    URL.revokeObjectURL(url);

                    // Show success message
                    alert(
                      `Successfully exported ${data.user_count} users to CSV file.`
                    );
                  } catch (error) {
                    alert(`Error exporting users: ${error.message}`);
                  }
                }}
              >
                <DownloadIcon className="h-4 w-4 mr-1" />
                Export
              </button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reset Password Dialog */}
      <Dialog
        open={showResetPasswordDialog}
        onOpenChange={(open) => {
          if (open) return;
          handleCancelResetPassword();
          setCreateError(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Reset Password</DialogTitle>
            <DialogDescription>
              Enter a new password for{' '}
              {resetPasswordUser?.usernameDisplay || 'this user'}.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4 py-4">
            <div className="grid gap-2">
              <label className="text-sm font-medium text-gray-700">
                New Password
              </label>
              <input
                type="password"
                className="border rounded px-3 py-2 w-full"
                placeholder="Enter new password"
                value={resetPassword}
                onChange={(e) => setResetPassword(e.target.value)}
                autoFocus
              />
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={handleCancelResetPassword}
              disabled={resetLoading}
            >
              Cancel
            </Button>
            <Button
              variant="default"
              onClick={handleResetPasswordSubmit}
              disabled={resetLoading || !resetPassword}
              className="bg-sky-600 text-white hover:bg-sky-700"
            >
              {resetLoading ? 'Resetting...' : 'Reset Password'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete User Confirmation Dialog */}
      <Dialog
        open={showDeleteConfirmDialog}
        onOpenChange={(open) => {
          if (open) return;
          handleCancelDelete();
          setCreateError(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete User</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete user &quot;
              {userToDelete?.usernameDisplay || 'this user'}&quot;? This action
              cannot be undone.
            </DialogDescription>
          </DialogHeader>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={handleCancelDelete}
              disabled={deleteLoading}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteUserConfirm}
              disabled={deleteLoading}
            >
              {deleteLoading ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function UsersTable({
  refreshInterval,
  setLoading,
  refreshDataRef,
  checkPermissionAndAct,
  roleLoading,
  onResetPassword,
  onDeleteUser,
  basicAuthEnabled,
  ingressBasicAuthEnabled,
  externalProxyAuthEnabled,
  currentUserRole,
  currentUserId,
  filters,
  setValueList,
  deduplicateUsers,
  setLastFetchedTime,
}) {
  const [usersWithCounts, setUsersWithCounts] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasInitiallyLoaded, setHasInitiallyLoaded] = useState(false);
  const [sortConfig, setSortConfig] = useState({
    key: 'username',
    direction: 'ascending',
  });
  const [editingUserId, setEditingUserId] = useState(null);
  const [currentEditingRole, setCurrentEditingRole] = useState('');

  // Lookup dictionary for GPU type and infra filtering
  // Structure: infra -> gpuType -> userId -> { clusterCount, jobCount, gpuCount }
  const [combinedLookup, setCombinedLookup] = useState({});
  const [lookupsReady, setLookupsReady] = useState(false);

  const fetchDataAndProcess = useCallback(
    async (showLoading = false) => {
      if (setLoading && showLoading) setLoading(true);
      if (showLoading) setIsLoading(true);
      setLookupsReady(false); // Reset lookups state when starting to fetch
      try {
        // Step 1: Load users first and show them immediately
        const usersData = await dashboardCache.get(getUsers);

        // Show users immediately with placeholder counts
        const initialProcessedUsers = (usersData || []).map((user) => ({
          ...user,
          usernameDisplay: parseUsername(user.username, user.userId),
          fullEmailID: getFullEmailID(user.username, user.userId),
          clusterCount: -1, // Use -1 as loading indicator
          jobCount: -1, // Use -1 as loading indicator
          gpuCount: -1, // Use -1 as loading indicator
        }));

        setUsersWithCounts(initialProcessedUsers);
        setHasInitiallyLoaded(true);

        // Clear loading indicators now that we have users
        if (setLoading && showLoading) setLoading(false);
        if (showLoading) setIsLoading(false);

        // Step 2: Load clusters and jobs in background and update counts
        let clustersData = [];
        let managedJobsResponse = { jobs: [] };
        try {
          [clustersData, managedJobsResponse] = await Promise.all([
            dashboardCache.get(getClusters),
            // Use shared cache key (no field filtering) - preloader uses same args
            dashboardCache.get(getManagedJobs, [
              { allUsers: true, skipFinished: true },
            ]),
          ]);
        } catch (error) {
          console.error('Error fetching clusters and managed jobs:', error);
        }

        const jobsData = managedJobsResponse.jobs || [];

        // Build combined lookup dictionary for GPU type and infra filtering
        // Structure: userId -> infra -> gpuType -> { clusterCount, jobCount, gpuCount }
        //            userId -> infra -> "Total" -> { clusterCount, jobCount, gpuCount }
        //            userId -> "Total" -> gpuType -> { clusterCount, jobCount, gpuCount }
        const newCombinedLookup = {};

        // Helper to extract GPU type from accelerators
        const extractGPUType = (accelerators) => {
          if (!accelerators) return null;

          let parsed = accelerators;
          if (typeof accelerators === 'string') {
            try {
              const jsonStr = accelerators
                .replace(/'/g, '"')
                .replace(/None/g, 'null');
              parsed = JSON.parse(jsonStr);
            } catch (e) {
              return null;
            }
          }

          if (typeof parsed === 'object' && parsed !== null) {
            const entries = Object.entries(parsed);
            if (entries.length > 0) {
              return entries[0][0]; // Return GPU type (the key)
            }
          }
          return null;
        };

        // Helper to update combined lookup
        const updateCombinedLookup = (
          userId,
          infra,
          gpuType,
          clusterDelta,
          jobDelta,
          gpuDelta
        ) => {
          if (!userId || !infra) return;

          // Initialize user structure
          if (!newCombinedLookup[userId]) {
            newCombinedLookup[userId] = {};
          }

          // Initialize infra structure
          if (!newCombinedLookup[userId][infra]) {
            newCombinedLookup[userId][infra] = {};
          }

          // Initialize cross-infra "Total" structure
          if (!newCombinedLookup[userId]['Total']) {
            newCombinedLookup[userId]['Total'] = {};
          }

          // Update infra -> "Total" (all resources in this infra)
          if (!newCombinedLookup[userId][infra]['Total']) {
            newCombinedLookup[userId][infra]['Total'] = {
              clusterCount: 0,
              jobCount: 0,
              gpuCount: 0,
            };
          }
          newCombinedLookup[userId][infra]['Total'].clusterCount +=
            clusterDelta;
          newCombinedLookup[userId][infra]['Total'].jobCount += jobDelta;
          newCombinedLookup[userId][infra]['Total'].gpuCount += gpuDelta;

          // Update infra -> gpuType (specific GPU type in this infra) if gpuType exists
          if (gpuType) {
            if (!newCombinedLookup[userId][infra][gpuType]) {
              newCombinedLookup[userId][infra][gpuType] = {
                clusterCount: 0,
                jobCount: 0,
                gpuCount: 0,
              };
            }
            newCombinedLookup[userId][infra][gpuType].clusterCount +=
              clusterDelta;
            newCombinedLookup[userId][infra][gpuType].jobCount += jobDelta;
            newCombinedLookup[userId][infra][gpuType].gpuCount += gpuDelta;

            // Update "Total" -> gpuType (cross-infra aggregates for this GPU type)
            if (!newCombinedLookup[userId]['Total'][gpuType]) {
              newCombinedLookup[userId]['Total'][gpuType] = {
                clusterCount: 0,
                jobCount: 0,
                gpuCount: 0,
              };
            }
            newCombinedLookup[userId]['Total'][gpuType].clusterCount +=
              clusterDelta;
            newCombinedLookup[userId]['Total'][gpuType].jobCount += jobDelta;
            newCombinedLookup[userId]['Total'][gpuType].gpuCount += gpuDelta;
          }
        };

        // Process clusters to build lookup
        for (const cluster of clustersData || []) {
          const userId = cluster.user_hash;
          if (!userId) continue;

          const gpuType = extractGPUType(cluster.gpus);
          const infra = cluster.infra;

          // Count GPUs (only from active clusters)
          let gpuCount = 0;
          if (cluster.status !== 'STOPPED' && cluster.status !== 'TERMINATED') {
            const gpuCountPerNode = getGPUCount(
              cluster.gpus,
              `Cluster ${cluster.cluster}`
            );
            // Multiply by number of nodes to get total GPU count
            const numNodes = cluster.num_nodes || 1;
            gpuCount = gpuCountPerNode * numNodes;
          }

          updateCombinedLookup(userId, infra, gpuType, 1, 0, gpuCount);
        }

        // Helper to extract num_nodes from cluster_resources_full (e.g., "3x(...)")
        const extractNumNodes = (clusterResourcesFull) => {
          if (
            !clusterResourcesFull ||
            typeof clusterResourcesFull !== 'string'
          ) {
            return 1;
          }
          const match = clusterResourcesFull.match(/^(\d+)x/);
          return match ? parseInt(match[1], 10) : 1;
        };

        // Process jobs to build lookup
        for (const job of jobsData || []) {
          if (!ACTIVE_JOB_STATUSES.has(job.status)) continue;

          const userId = job.user_hash;
          if (!userId) continue;

          const gpuType = extractGPUType(job.accelerators);
          const infra = job.infra;
          const gpuCountPerNode = getGPUCount(
            job.accelerators,
            `Job ${job.job_id}`
          );

          // Multiply by number of nodes to get total GPU count
          const numNodes = extractNumNodes(job.resources_str_full);
          const gpuCount = gpuCountPerNode * numNodes;

          updateCombinedLookup(userId, infra, gpuType, 0, 1, gpuCount);
        }

        // Store the lookup dictionary
        setCombinedLookup(newCombinedLookup);
        setLookupsReady(true); // Mark lookups as ready

        // Update users with actual counts (without filter applied)
        const finalProcessedUsers = (usersData || []).map((user) => {
          let clusterCount = 0;
          let clusterGPUCount = 0;
          let jobCount = 0;
          let jobGPUCount = 0;

          // Count clusters and sum GPUs in one pass (exclude STOPPED and TERMINATED clusters from GPU count)
          for (const cluster of clustersData || []) {
            if (cluster.user_hash === user.userId) {
              clusterCount++;
              // Only count GPUs from active clusters (exclude STOPPED and TERMINATED)
              if (
                cluster.status !== 'STOPPED' &&
                cluster.status !== 'TERMINATED'
              ) {
                const gpuCountPerNode = getGPUCount(
                  cluster.gpus,
                  `Cluster ${cluster.cluster}`
                );
                // Multiply by number of nodes to get total GPU count
                const numNodes = cluster.num_nodes || 1;
                clusterGPUCount += gpuCountPerNode * numNodes;
              }
            }
          }

          // Count active jobs and sum GPUs in one pass
          for (const job of jobsData || []) {
            if (
              job.user_hash === user.userId &&
              ACTIVE_JOB_STATUSES.has(job.status)
            ) {
              jobCount++;
              const gpuCountPerNode = getGPUCount(
                job.accelerators,
                `Job ${job.job_id}`
              );
              // Multiply by number of nodes to get total GPU count
              const numNodes = extractNumNodes(job.resources_str_full);
              jobGPUCount += gpuCountPerNode * numNodes;
            }
          }

          return {
            ...user,
            usernameDisplay: parseUsername(user.username, user.userId),
            fullEmailID: getFullEmailID(user.username, user.userId),
            clusterCount,
            jobCount,
            gpuCount: clusterGPUCount + jobGPUCount,
          };
        });

        // Collect unique GPU types and infra values for filter dropdowns
        const infras = new Set();
        const gpuTypes = new Set();

        for (const userLookup of Object.values(newCombinedLookup)) {
          // Collect infras (skip "Total" key)
          for (const infra of Object.keys(userLookup)) {
            if (infra !== 'Total') {
              infras.add(infra);
            }
          }
          // Collect GPU types from cross-infra "Total"
          if (userLookup['Total']) {
            for (const gpuType of Object.keys(userLookup['Total'])) {
              gpuTypes.add(gpuType);
            }
          }
        }

        // Update valueList for filter autocomplete
        const names = new Set();
        const userIds = new Set();
        const roles = new Set();

        finalProcessedUsers.forEach((user) => {
          if (user.usernameDisplay) names.add(user.usernameDisplay);
          if (user.userId) userIds.add(user.userId);
          if (user.role) roles.add(user.role);
        });

        setValueList({
          name: Array.from(names).sort(),
          'user id': Array.from(userIds).sort(),
          role: Array.from(roles).sort(),
          'gpu type': Array.from(gpuTypes).sort(),
          infra: Array.from(infras).sort(),
        });

        setUsersWithCounts(finalProcessedUsers);
      } catch (error) {
        console.error('Failed to fetch or process user data:', error);
        setUsersWithCounts([]);
        setHasInitiallyLoaded(true);
        if (setLoading && showLoading) setLoading(false);
        if (showLoading) setIsLoading(false);
      } finally {
        if (setLastFetchedTime) setLastFetchedTime(new Date());
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [setLoading, setLastFetchedTime]
  );

  useEffect(() => {
    if (refreshDataRef) {
      refreshDataRef.current = () => fetchDataAndProcess(true); // Show loading on manual refresh
    }
  }, [refreshDataRef, fetchDataAndProcess]);

  useEffect(() => {
    const initializeData = async () => {
      // Reset loading state when component mounts
      setHasInitiallyLoaded(false);
      setIsLoading(true);

      // Trigger cache preloading for users page and background preload other pages
      await cachePreloader.preloadForPage('users');

      fetchDataAndProcess(true); // Show loading on initial load
    };

    initializeData();

    const interval = setInterval(() => {
      if (window.document.visibilityState === 'visible') {
        fetchDataAndProcess(false); // Don't show loading on background refresh
      }
    }, refreshInterval);
    return () => clearInterval(interval);
  }, [fetchDataAndProcess, refreshInterval]);

  const filteredAndSortedUsers = useMemo(() => {
    let filtered = usersWithCounts;

    // Separate GPU type and infra filters from standard filters
    // Note: filter.property contains the label (e.g., "GPU", "Infra"), not the value
    const standardFilters = filters.filter(
      (f) => f.property !== 'GPU' && f.property !== 'Infra'
    );
    const gpuTypeFilters = filters.filter((f) => f.property === 'GPU');
    const infraFilters = filters.filter((f) => f.property === 'Infra');

    // Apply standard filters using the shared filter system
    if (standardFilters.length > 0) {
      filtered = filterData(
        usersWithCounts.map((user) => ({
          ...user,
          name: user.usernameDisplay,
          'user id': user.userId, // Note: space to match "User ID" -> "user id" from toLowerCase()
        })),
        standardFilters
      );
    }

    // Helper to get counts from lookup for a user given filter criteria
    // gpuTypeFilters and infraFilters are arrays - we OR within same type, AND across types
    const getFilteredCounts = (
      userId,
      gpuTypeFilterValues,
      infraFilterValues
    ) => {
      let clusterCount = 0;
      let jobCount = 0;
      let gpuCount = 0;

      const userLookup = combinedLookup[userId];
      if (!userLookup) {
        return { clusterCount: 0, jobCount: 0, gpuCount: 0 };
      }

      // Normalize filter values to lowercase
      const normalizedGpuTypes = gpuTypeFilterValues.map((v) =>
        v.toLowerCase()
      );
      const normalizedInfras = infraFilterValues.map((v) => v.toLowerCase());

      const hasGpuTypeFilters = normalizedGpuTypes.length > 0;
      const hasInfraFilters = normalizedInfras.length > 0;

      // Case 1: Both GPU and Infra filters (AND between types, OR within types)
      if (hasGpuTypeFilters && hasInfraFilters) {
        for (const infraFilter of normalizedInfras) {
          for (const [infra, gpuTypeMap] of Object.entries(userLookup)) {
            if (infra === 'Total') continue;
            if (infra.toLowerCase() !== infraFilter) continue;

            for (const gpuTypeFilter of normalizedGpuTypes) {
              for (const [gpuType, counts] of Object.entries(gpuTypeMap)) {
                if (gpuType === 'Total') continue;
                if (gpuType.toLowerCase() === gpuTypeFilter) {
                  clusterCount += counts.clusterCount;
                  jobCount += counts.jobCount;
                  gpuCount += counts.gpuCount;
                }
              }
            }
          }
        }
      }
      // Case 2: Infra only
      else if (hasInfraFilters) {
        for (const infraFilter of normalizedInfras) {
          for (const [infra, gpuTypeMap] of Object.entries(userLookup)) {
            if (infra === 'Total') continue;
            if (infra.toLowerCase() === infraFilter && gpuTypeMap['Total']) {
              const counts = gpuTypeMap['Total'];
              clusterCount += counts.clusterCount;
              jobCount += counts.jobCount;
              gpuCount += counts.gpuCount;
            }
          }
        }
      }
      // Case 3: GPU type only
      else if (hasGpuTypeFilters) {
        if (userLookup['Total']) {
          for (const gpuTypeFilter of normalizedGpuTypes) {
            for (const [gpuType, counts] of Object.entries(
              userLookup['Total']
            )) {
              if (gpuType.toLowerCase() === gpuTypeFilter) {
                clusterCount += counts.clusterCount;
                jobCount += counts.jobCount;
                gpuCount += counts.gpuCount;
              }
            }
          }
        }
      }

      return { clusterCount, jobCount, gpuCount };
    };

    // Apply GPU type and infra filters
    const hasGpuTypeFilter = gpuTypeFilters.length > 0;
    const hasInfraFilter = infraFilters.length > 0;

    if (hasGpuTypeFilter || hasInfraFilter) {
      // Extract filter values - support multiple filters of same type (OR logic)
      const gpuTypeFilterValues = gpuTypeFilters
        .map((f) => f.value)
        .filter(Boolean);
      const infraFilterValues = infraFilters
        .map((f) => f.value)
        .filter(Boolean);

      // Normalize to lowercase for matching
      const normalizedGpuTypes = gpuTypeFilterValues.map((v) =>
        v.toLowerCase()
      );
      const normalizedInfras = infraFilterValues.map((v) => v.toLowerCase());

      // Filter users: check if they have ANY resources matching the filters
      filtered = filtered.filter((user) => {
        const userLookup = combinedLookup[user.userId];
        if (!userLookup) return false;

        // Case 1: Both GPU and Infra filters
        if (hasGpuTypeFilter && hasInfraFilter) {
          for (const infraFilter of normalizedInfras) {
            for (const [infra, gpuTypeMap] of Object.entries(userLookup)) {
              if (infra === 'Total') continue;
              if (infra.toLowerCase() !== infraFilter) continue;

              for (const gpuTypeFilter of normalizedGpuTypes) {
                for (const gpuType of Object.keys(gpuTypeMap)) {
                  if (gpuType === 'Total') continue;
                  if (gpuType.toLowerCase() === gpuTypeFilter) {
                    return true;
                  }
                }
              }
            }
          }
        }
        // Case 2: Infra only
        else if (hasInfraFilter) {
          for (const infraFilter of normalizedInfras) {
            for (const infra of Object.keys(userLookup)) {
              if (infra === 'Total') continue;
              if (infra.toLowerCase() === infraFilter) {
                return true;
              }
            }
          }
        }
        // Case 3: GPU type only
        else if (hasGpuTypeFilter) {
          if (userLookup['Total']) {
            for (const gpuTypeFilter of normalizedGpuTypes) {
              for (const gpuType of Object.keys(userLookup['Total'])) {
                if (gpuType.toLowerCase() === gpuTypeFilter) {
                  return true;
                }
              }
            }
          }
        }

        return false;
      });

      // Update counts for filtered users
      filtered = filtered.map((user) => {
        const filteredCounts = getFilteredCounts(
          user.userId,
          gpuTypeFilterValues,
          infraFilterValues
        );

        return {
          ...user,
          clusterCount: filteredCounts.clusterCount,
          jobCount: filteredCounts.jobCount,
          gpuCount: filteredCounts.gpuCount,
        };
      });
    }

    // Deduplicate by username if toggle is enabled
    if (deduplicateUsers) {
      const deduped = {};
      filtered.forEach((user) => {
        const name = user.usernameDisplay;
        if (!deduped[name]) {
          // Initialize with first user
          deduped[name] = {
            ...user,
            // Track all userIds for this username
            userIds: [user.userId],
            // Track counts that will be summed
            clusterCount: user.clusterCount,
            jobCount: user.jobCount,
            gpuCount: user.gpuCount,
            // Track the oldest created_at
            created_at: user.created_at,
          };
        } else {
          // Merge with existing entry
          deduped[name].userIds.push(user.userId);

          // Sum cluster counts (handle loading state smartly)
          if (user.clusterCount !== -1) {
            // If current user has a valid count
            if (deduped[name].clusterCount === -1) {
              // Replace loading state with actual count
              deduped[name].clusterCount = user.clusterCount;
            } else {
              // Add to existing valid count
              deduped[name].clusterCount += user.clusterCount;
            }
          }
          // If user.clusterCount === -1 and deduped already has valid count, keep existing

          // Sum job counts (same logic)
          if (user.jobCount !== -1) {
            if (deduped[name].jobCount === -1) {
              deduped[name].jobCount = user.jobCount;
            } else {
              deduped[name].jobCount += user.jobCount;
            }
          }

          // Sum GPU counts (same logic)
          if (user.gpuCount !== -1) {
            if (deduped[name].gpuCount === -1) {
              deduped[name].gpuCount = user.gpuCount;
            } else {
              deduped[name].gpuCount += user.gpuCount;
            }
          }

          // Keep the oldest created_at
          if (
            user.created_at &&
            (!deduped[name].created_at ||
              user.created_at < deduped[name].created_at)
          ) {
            deduped[name].created_at = user.created_at;
          }
        }
      });
      filtered = Object.values(deduped);
    }

    return sortData(filtered, sortConfig.key, sortConfig.direction);
  }, [usersWithCounts, sortConfig, filters, deduplicateUsers, combinedLookup]);

  const requestSort = (key) => {
    let direction = 'ascending';
    if (sortConfig.key === key && sortConfig.direction === 'ascending') {
      direction = 'descending';
    }
    setSortConfig({ key, direction });
  };

  const getSortDirection = (key) => {
    if (sortConfig.key === key) {
      return sortConfig.direction === 'ascending' ? ' ↑' : ' ↓';
    }
    return '';
  };

  const handleEditClick = async (userId, currentRole) => {
    await checkPermissionAndAct('cannot edit user role', () => {
      setEditingUserId(userId);
      setCurrentEditingRole(currentRole);
    });
  };

  const handleCancelEdit = () => {
    setEditingUserId(null);
    setCurrentEditingRole('');
  };

  const handleSaveEdit = async (userId) => {
    if (!userId || !currentEditingRole) {
      console.error('User ID or role is missing.');
      alert('Error: User ID or role is missing.');
      return;
    }
    setIsLoading(true); // Or use parent setLoading
    try {
      const response = await apiClient.post(`/users/update`, {
        user_id: userId,
        role: currentEditingRole,
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to update role');
      }
      // Invalidate cache before fetching new data
      dashboardCache.invalidate(getUsers);
      await fetchDataAndProcess(true); // Refresh data
      handleCancelEdit(); // Exit edit mode
    } catch (error) {
      console.error('Failed to update user role:', error);
      alert(`Error updating role: ${error.message}`);
    } finally {
      setIsLoading(false); // Or use parent setLoading
    }
  };

  if (isLoading && usersWithCounts.length === 0 && !hasInitiallyLoaded) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress />
      </div>
    );
  }

  if (!hasInitiallyLoaded) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress />
        <span className="ml-2 text-gray-500">Loading users...</span>
      </div>
    );
  }

  // Check if we're still loading lookups for GPU/Infra filters
  const hasGpuOrInfraFilters = filters.some(
    (f) => f.property === 'GPU' || f.property === 'Infra'
  );
  if (hasGpuOrInfraFilters && !lookupsReady) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress />
        <span className="ml-2 text-gray-500">Loading filtered data...</span>
      </div>
    );
  }

  if (!filteredAndSortedUsers || filteredAndSortedUsers.length === 0) {
    return (
      <div className="text-center py-12">
        <p className="text-lg font-semibold text-gray-500">
          {filters.length > 0
            ? 'No users match your filters.'
            : 'No users found.'}
        </p>
        <p className="text-sm text-gray-400 mt-1">
          {filters.length > 0
            ? 'Try adjusting your filter criteria.'
            : 'There are currently no users to display.'}
        </p>
      </div>
    );
  }

  return (
    <Card>
      <div className="overflow-x-auto rounded-lg">
        <Table className="min-w-full">
          <TableHeader>
            <TableRow>
              <TableHead
                onClick={() => requestSort('usernameDisplay')}
                className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/6"
              >
                Name{getSortDirection('usernameDisplay')}
              </TableHead>
              {!deduplicateUsers && (
                <TableHead
                  onClick={() => requestSort('fullEmailID')}
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/6"
                >
                  User ID{getSortDirection('fullEmailID')}
                </TableHead>
              )}
              {!deduplicateUsers && !ingressBasicAuthEnabled && (
                <TableHead
                  onClick={() => requestSort('role')}
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/6"
                >
                  Role{getSortDirection('role')}
                </TableHead>
              )}
              {!deduplicateUsers &&
                !ingressBasicAuthEnabled &&
                !externalProxyAuthEnabled && (
                  <TableHead
                    onClick={() => requestSort('userType')}
                    className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/6"
                  >
                    Type{getSortDirection('userType')}
                  </TableHead>
                )}
              <TableHead
                onClick={() => requestSort('created_at')}
                className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/6"
              >
                Joined{getSortDirection('created_at')}
              </TableHead>
              <TableHead
                onClick={() => requestSort('gpuCount')}
                className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/6"
              >
                GPUs{getSortDirection('gpuCount')}
              </TableHead>
              <TableHead
                onClick={() => requestSort('clusterCount')}
                className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/6"
              >
                Clusters{getSortDirection('clusterCount')}
              </TableHead>
              <TableHead
                onClick={() => requestSort('jobCount')}
                className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/6"
              >
                Jobs{getSortDirection('jobCount')}
              </TableHead>
              {/* Show Actions column if basicAuthEnabled and not deduplicating */}
              {!deduplicateUsers &&
                (basicAuthEnabled || currentUserRole === 'admin') && (
                  <TableHead className="whitespace-nowrap w-1/7">
                    Actions
                  </TableHead>
                )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {filteredAndSortedUsers.map((user) => {
              const isSystemUser = user.userType === 'system';
              const isBasicUser = user.userType === 'basic';
              const canResetPassword =
                isBasicUser &&
                (currentUserRole === 'admin' || user.userId === currentUserId);
              return (
                <TableRow key={user.userId}>
                  <TableCell className="truncate" title={user.username}>
                    {user.usernameDisplay}
                  </TableCell>
                  {!deduplicateUsers && (
                    <TableCell className="truncate" title={user.fullEmailID}>
                      {user.fullEmailID}
                    </TableCell>
                  )}
                  {!deduplicateUsers && !ingressBasicAuthEnabled && (
                    <TableCell className="truncate" title={user.role}>
                      <div className="flex items-center gap-2">
                        {editingUserId === user.userId ? (
                          <>
                            <select
                              value={currentEditingRole}
                              onChange={(e) =>
                                setCurrentEditingRole(e.target.value)
                              }
                              className="block w-auto p-1 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-sky-blue focus:border-sky-blue sm:text-sm"
                            >
                              <option value="admin">Admin</option>
                              <option value="user">User</option>
                            </select>
                            <button
                              onClick={() => handleSaveEdit(user.userId)}
                              className="text-green-600 hover:text-green-800 p-1"
                              title="Save"
                            >
                              <CheckIcon className="h-4 w-4" />
                            </button>
                            <button
                              onClick={handleCancelEdit}
                              className="text-gray-500 hover:text-gray-700 p-1"
                              title="Cancel"
                            >
                              <XIcon className="h-4 w-4" />
                            </button>
                          </>
                        ) : (
                          <>
                            <span className="capitalize">{user.role}</span>
                            {/* Only show edit role button if admin and not a system user */}
                            {currentUserRole === 'admin' && (
                              <button
                                onClick={
                                  !isSystemUser
                                    ? () =>
                                        handleEditClick(user.userId, user.role)
                                    : undefined
                                }
                                className={
                                  !isSystemUser
                                    ? 'text-blue-600 hover:text-blue-700 p-1'
                                    : 'text-gray-300 cursor-not-allowed p-1'
                                }
                                title={
                                  !isSystemUser
                                    ? 'Edit role'
                                    : 'Cannot edit role for system users'
                                }
                                disabled={isSystemUser}
                              >
                                <PenIcon className="h-3 w-3" />
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </TableCell>
                  )}
                  {!deduplicateUsers &&
                    !ingressBasicAuthEnabled &&
                    !externalProxyAuthEnabled && (
                      <TableCell className="truncate" title={user.userType}>
                        <span className="capitalize">
                          {user.userType === 'sso' ? 'SSO' : user.userType}
                        </span>
                      </TableCell>
                    )}
                  <TableCell className="truncate">
                    {user.created_at ? (
                      <TimestampWithTooltip
                        date={new Date(user.created_at * 1000)}
                      />
                    ) : (
                      '-'
                    )}
                  </TableCell>
                  <TableCell>
                    {user.gpuCount === -1 ? (
                      <span className="px-2 py-0.5 bg-gray-100 text-gray-500 rounded text-xs font-medium">
                        <CircularProgress size={12} />
                      </span>
                    ) : (
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-medium ${
                          user.gpuCount > 0
                            ? 'bg-purple-100 text-purple-600'
                            : 'bg-gray-100 text-gray-500'
                        }`}
                        title={`Total GPUs: ${user.gpuCount}`}
                      >
                        {user.gpuCount}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {user.clusterCount === -1 ? (
                      <span className="px-2 py-0.5 bg-gray-100 text-gray-500 rounded text-xs font-medium">
                        <CircularProgress size={12} />
                      </span>
                    ) : (
                      <Link
                        href={`/clusters?property=user&operator=%3A&value=${encodeURIComponent(user.username)}`}
                        className={`px-2 py-0.5 rounded text-xs font-medium transition-colors duration-200 cursor-pointer inline-block ${
                          user.clusterCount > 0
                            ? 'bg-blue-100 text-blue-600 hover:bg-blue-200 hover:text-blue-700'
                            : 'bg-gray-100 text-gray-500 hover:bg-gray-200 hover:text-gray-700'
                        }`}
                        title={`View ${user.clusterCount} cluster${user.clusterCount !== 1 ? 's' : ''} for ${user.usernameDisplay}`}
                      >
                        {user.clusterCount}
                      </Link>
                    )}
                  </TableCell>
                  <TableCell>
                    {user.jobCount === -1 ? (
                      <span className="px-2 py-0.5 bg-gray-100 text-gray-500 rounded text-xs font-medium">
                        <CircularProgress size={12} />
                      </span>
                    ) : (
                      <Link
                        href={`/jobs?property=user&operator=%3A&value=${encodeURIComponent(user.username)}`}
                        className={`px-2 py-0.5 rounded text-xs font-medium transition-colors duration-200 cursor-pointer inline-block ${
                          user.jobCount > 0
                            ? 'bg-green-100 text-green-600 hover:bg-green-200 hover:text-green-700'
                            : 'bg-gray-100 text-gray-500 hover:bg-gray-200 hover:text-gray-700'
                        }`}
                        title={`View ${user.jobCount} active job${user.jobCount !== 1 ? 's' : ''} for ${user.usernameDisplay}`}
                      >
                        {user.jobCount}
                      </Link>
                    )}
                  </TableCell>
                  {/* Actions cell logic - hide when deduplicating */}
                  {!deduplicateUsers &&
                    (basicAuthEnabled || currentUserRole === 'admin') && (
                      <TableCell className="relative">
                        <div className="flex items-center gap-2">
                          {/* Reset password icon: admin can reset any basic user, user can only reset self (basic auth only) */}
                          {basicAuthEnabled && (
                            <button
                              onClick={
                                canResetPassword
                                  ? async () => {
                                      onResetPassword(user);
                                    }
                                  : undefined
                              }
                              className={
                                canResetPassword
                                  ? 'text-blue-600 hover:text-blue-700 p-1'
                                  : 'text-gray-300 cursor-not-allowed p-1'
                              }
                              title={
                                !isBasicUser
                                  ? 'Password reset only available for basic auth users'
                                  : canResetPassword
                                    ? 'Reset Password'
                                    : 'You can only reset your own password'
                              }
                              disabled={!canResetPassword}
                            >
                              <KeyRoundIcon className="h-4 w-4" />
                            </button>
                          )}
                          {/* Delete button - only show for admin, disabled for system users */}
                          {currentUserRole === 'admin' && (
                            <button
                              onClick={
                                !isSystemUser
                                  ? () => onDeleteUser(user)
                                  : undefined
                              }
                              className={
                                !isSystemUser
                                  ? 'text-red-600 hover:text-red-700 p-1'
                                  : 'text-gray-300 cursor-not-allowed p-1'
                              }
                              title={
                                !isSystemUser
                                  ? 'Delete User'
                                  : 'Cannot delete system users'
                              }
                              disabled={isSystemUser}
                            >
                              <Trash2Icon className="h-4 w-4" />
                            </button>
                          )}
                        </div>
                      </TableCell>
                    )}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </Card>
  );
}

UsersTable.propTypes = {
  refreshInterval: PropTypes.number.isRequired,
  setLoading: PropTypes.func.isRequired,
  refreshDataRef: PropTypes.shape({
    current: PropTypes.func,
  }).isRequired,
  checkPermissionAndAct: PropTypes.func.isRequired,
  roleLoading: PropTypes.bool.isRequired,
  onResetPassword: PropTypes.func.isRequired,
  onDeleteUser: PropTypes.func.isRequired,
  basicAuthEnabled: PropTypes.bool,
  ingressBasicAuthEnabled: PropTypes.bool,
  externalProxyAuthEnabled: PropTypes.bool,
  currentUserRole: PropTypes.string,
  currentUserId: PropTypes.string,
  setLastFetchedTime: PropTypes.func,
};

// Service Account Tokens Management Component
function ServiceAccountTokensView({
  checkPermissionAndAct,
  userRoleCache,
  setCreateSuccess,
  setCreateError,
  showCreateDialog,
  setShowCreateDialog,
  showRotateDialog,
  setShowRotateDialog,
  tokenToRotate,
  setTokenToRotate,
  rotating,
  setRotating,
  searchQuery,
  setSearchQuery,
}) {
  const [tokens, setTokens] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [tokenToDelete, setTokenToDelete] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [newToken, setNewToken] = useState({
    token_name: '',
    expires_in_days: 30,
  });
  const [rotateExpiration, setRotateExpiration] = useState('');
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [copySuccess, setCopySuccess] = useState('');

  // Add new state for tokens displayed within dialogs
  const [createdTokenInDialog, setCreatedTokenInDialog] = useState(null);
  const [rotatedTokenInDialog, setRotatedTokenInDialog] = useState(null);

  // Role editing state
  const [editingTokenId, setEditingTokenId] = useState(null);
  const [currentEditingRole, setCurrentEditingRole] = useState('');

  // Enhanced tokens with cluster/job counts
  const [tokensWithCounts, setTokensWithCounts] = useState([]);

  // Fetch tokens and related data
  const fetchTokensAndCounts = async (forceRefresh = false) => {
    try {
      setLoading(true);

      // Invalidate cache if force refresh requested (after mutations)
      if (forceRefresh) {
        dashboardCache.invalidate(getServiceAccountTokens);
      }

      // Step 1: Fetch service account tokens (using cache)
      const tokensData = await dashboardCache.get(getServiceAccountTokens);
      setTokens(tokensData || []);

      // Step 2: Fetch clusters and jobs data in parallel
      let clustersResponse = [];
      let jobsResponse = { jobs: [] };
      try {
        [clustersResponse, jobsResponse] = await Promise.all([
          dashboardCache.get(getClusters),
          // Use shared cache key (no field filtering) - preloader uses same args
          dashboardCache.get(getManagedJobs, [
            { allUsers: true, skipFinished: true },
          ]),
        ]);
      } catch (error) {
        console.error('Error fetching clusters and managed jobs:', error);
      }

      const clustersData = clustersResponse || [];
      const jobsData = jobsResponse?.jobs || [];

      // Step 3: Calculate counts for each service account
      const enhancedTokens = (tokensData || []).map((token) => {
        const serviceAccountId = token.service_account_user_id;
        let clusterCount = 0;
        let clusterGPUCount = 0;
        let jobCount = 0;
        let jobGPUCount = 0;

        // Count clusters and sum GPUs in one pass (exclude STOPPED and TERMINATED clusters from GPU count)
        for (const cluster of clustersData) {
          if (cluster.user_hash === serviceAccountId) {
            clusterCount++;
            // Only count GPUs from active clusters (exclude STOPPED and TERMINATED)
            if (
              cluster.status !== 'STOPPED' &&
              cluster.status !== 'TERMINATED'
            ) {
              clusterGPUCount += getGPUCount(
                cluster.gpus,
                `Cluster ${cluster.cluster}`
              );
            }
          }
        }

        // Count active jobs and sum GPUs in one pass
        for (const job of jobsData) {
          if (
            job.user_hash === serviceAccountId &&
            ACTIVE_JOB_STATUSES.has(job.status)
          ) {
            jobCount++;
            jobGPUCount += getGPUCount(
              job.accelerators,
              `Job ${job.job_name || job.job_id}`
            );
          }
        }

        return {
          ...token,
          clusterCount,
          jobCount,
          gpuCount: clusterGPUCount + jobGPUCount,
          // Extract primary role
          primaryRole:
            token.service_account_roles &&
            token.service_account_roles.length > 0
              ? token.service_account_roles[0]
              : 'user',
        };
      });

      setTokensWithCounts(enhancedTokens);
    } catch (error) {
      console.error('Error fetching tokens and counts:', error);
      setTokens([]);
      setTokensWithCounts([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTokensAndCounts();
  }, []);

  // Role editing functions
  const handleEditClick = async (tokenId, currentRole) => {
    await checkPermissionAndAct('cannot edit service account role', () => {
      setEditingTokenId(tokenId);
      setCurrentEditingRole(currentRole);
    });
  };

  const handleCancelEdit = () => {
    setEditingTokenId(null);
    setCurrentEditingRole('');
  };

  const handleSaveEdit = async (tokenId) => {
    if (!tokenId || !currentEditingRole) {
      console.error('Token ID or role is missing.');
      setCreateError(new Error('Token ID or role is missing.'));
      return;
    }

    setLoading(true);
    try {
      const response = await apiClient.post(
        '/users/service-account-tokens/update-role',
        {
          token_id: tokenId,
          role: currentEditingRole,
        }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to update role');
      }

      setCreateSuccess('Service account role updated successfully!');
      await fetchTokensAndCounts(true); // Refresh data (force refresh after mutation)
      handleCancelEdit(); // Exit edit mode
    } catch (error) {
      console.error('Failed to update service account role:', error);
      setCreateError(error);
    } finally {
      setLoading(false);
    }
  };

  // Copy to clipboard
  const copyToClipboard = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopySuccess('Copied!');
      setTimeout(() => setCopySuccess(''), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  // Handle create token
  const handleCreateToken = async () => {
    if (!newToken.token_name.trim()) {
      setCreateError(new Error('Token name is required'));
      return;
    }

    setCreating(true);
    try {
      const payload = {
        token_name: newToken.token_name.trim(),
        expires_in_days:
          newToken.expires_in_days === '' ? null : newToken.expires_in_days,
      };

      const response = await apiClient.post(
        '/users/service-account-tokens',
        payload
      );

      if (response.ok) {
        const data = await response.json();
        setCreatedTokenInDialog(data.token);
        setNewToken({ token_name: '', expires_in_days: 30 });
        await fetchTokensAndCounts(true); // Force refresh after creation
      } else {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to create token');
      }
    } catch (error) {
      setCreateError(error);
    } finally {
      setCreating(false);
    }
  };

  // Handle delete token
  const handleDeleteToken = async () => {
    if (!tokenToDelete) return;

    setDeleting(true);
    setDeleteError(null);
    try {
      const response = await apiClient.post(
        '/users/service-account-tokens/delete',
        {
          token_id: tokenToDelete.token_id,
        }
      );

      if (response.ok) {
        setCreateSuccess(
          `Service account "${tokenToDelete.token_name}" deleted successfully!`
        );
        setShowDeleteDialog(false);
        setTokenToDelete(null);
        setDeleteError(null);
        await fetchTokensAndCounts(true); // Force refresh after deletion
      } else {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to delete service account');
      }
    } catch (error) {
      // Show error at top level for better visibility
      setShowDeleteDialog(false);
      setTokenToDelete(null);
      setDeleteError(null);
      setCreateError(error);
    } finally {
      setDeleting(false);
    }
  };

  // Handle rotate token
  const handleRotateToken = async () => {
    if (!tokenToRotate) return;

    setRotating(true);
    try {
      const payload = {
        token_id: tokenToRotate.token_id,
        expires_in_days:
          rotateExpiration === '' ? null : parseInt(rotateExpiration),
      };

      const response = await apiClient.post(
        '/users/service-account-tokens/rotate',
        payload
      );

      if (response.ok) {
        const data = await response.json();
        setRotatedTokenInDialog(data.token);
        await fetchTokensAndCounts(true); // Force refresh after rotation
      } else {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to rotate token');
      }
    } catch (error) {
      setCreateError(error);
    } finally {
      setRotating(false);
    }
  };

  // Filter tokens based on search query
  const filteredTokens = tokensWithCounts.filter((token) => {
    if (!searchQuery?.trim()) return true;

    const query = searchQuery.toLowerCase();
    return (
      token.token_name?.toLowerCase().includes(query) ||
      token.creator_name?.toLowerCase().includes(query) ||
      token.service_account_name?.toLowerCase().includes(query) ||
      token.primaryRole?.toLowerCase().includes(query)
    );
  });

  if (loading && tokensWithCounts.length === 0) {
    return (
      <div className="flex items-center justify-center py-8">
        <CircularProgress size={32} />
        <span className="ml-3">Loading tokens...</span>
      </div>
    );
  }

  return (
    <>
      {/* Tokens Table */}
      {filteredTokens.length === 0 ? (
        <div className="text-center py-12">
          <KeyRoundIcon className="mx-auto h-12 w-12 text-gray-400" />
          <h3 className="mt-2 text-sm font-medium text-gray-900">
            {searchQuery?.trim()
              ? 'No tokens match your search'
              : 'No service accounts'}
          </h3>
          <p className="mt-1 text-sm text-gray-500">
            {searchQuery?.trim()
              ? 'Try adjusting your search terms.'
              : 'No service accounts have been created yet.'}
          </p>
        </div>
      ) : (
        <>
          <Card>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Created by</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Clusters</TableHead>
                  <TableHead>Jobs</TableHead>
                  <TableHead>GPUs</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead>Expires</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredTokens.map((token) => (
                  <TableRow key={token.token_id}>
                    <TableCell className="truncate" title={token.token_name}>
                      {token.token_name}
                    </TableCell>
                    <TableCell className="truncate">
                      <div className="flex items-center">
                        <span>{token.creator_name || 'Unknown'}</span>
                        {token.creator_user_hash !== userRoleCache?.id && (
                          <span className="ml-2 px-1.5 py-0.5 text-xs bg-gray-100 text-gray-600 rounded">
                            Other
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="truncate">
                      <div className="flex items-center gap-2">
                        {editingTokenId === token.token_id ? (
                          <>
                            <select
                              value={currentEditingRole}
                              onChange={(e) =>
                                setCurrentEditingRole(e.target.value)
                              }
                              className="block w-auto p-1 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-sky-blue focus:border-sky-blue sm:text-sm"
                            >
                              <option value="admin">Admin</option>
                              <option value="user">User</option>
                            </select>
                            <button
                              onClick={() => handleSaveEdit(token.token_id)}
                              className="text-green-600 hover:text-green-800 p-1"
                              title="Save"
                            >
                              <CheckIcon className="h-4 w-4" />
                            </button>
                            <button
                              onClick={handleCancelEdit}
                              className="text-gray-500 hover:text-gray-700 p-1"
                              title="Cancel"
                            >
                              <XIcon className="h-4 w-4" />
                            </button>
                          </>
                        ) : (
                          <>
                            <span className="capitalize">
                              {token.primaryRole}
                            </span>
                            {/* Only show edit role button if admin or owner */}
                            {(userRoleCache?.role === 'admin' ||
                              token.creator_user_hash ===
                                userRoleCache?.id) && (
                              <button
                                onClick={() =>
                                  handleEditClick(
                                    token.token_id,
                                    token.primaryRole
                                  )
                                }
                                className="text-blue-600 hover:text-blue-700 p-1"
                                title="Edit role"
                              >
                                <PenIcon className="h-3 w-3" />
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/clusters?property=user&operator=%3A&value=${encodeURIComponent(token.service_account_name)}`}
                        className={`px-2 py-0.5 rounded text-xs font-medium transition-colors duration-200 cursor-pointer inline-block ${
                          token.clusterCount > 0
                            ? 'bg-blue-100 text-blue-600 hover:bg-blue-200 hover:text-blue-700'
                            : 'bg-gray-100 text-gray-500 hover:bg-gray-200 hover:text-gray-700'
                        }`}
                        title={`View ${token.clusterCount} cluster${token.clusterCount !== 1 ? 's' : ''} for ${token.token_name}`}
                      >
                        {token.clusterCount}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/jobs?property=user&operator=%3A&value=${encodeURIComponent(token.service_account_name)}`}
                        className={`px-2 py-0.5 rounded text-xs font-medium transition-colors duration-200 cursor-pointer inline-block ${
                          token.jobCount > 0
                            ? 'bg-green-100 text-green-600 hover:bg-green-200 hover:text-green-700'
                            : 'bg-gray-100 text-gray-500 hover:bg-gray-200 hover:text-gray-700'
                        }`}
                        title={`View ${token.jobCount} active job${token.jobCount !== 1 ? 's' : ''} for ${token.token_name}`}
                      >
                        {token.jobCount}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-medium ${
                          token.gpuCount > 0
                            ? 'bg-purple-100 text-purple-600'
                            : 'bg-gray-100 text-gray-500'
                        }`}
                        title={`Total GPUs: ${token.gpuCount}`}
                      >
                        {token.gpuCount}
                      </span>
                    </TableCell>
                    <TableCell className="truncate">
                      {token.created_at ? (
                        <TimestampWithTooltip
                          date={new Date(token.created_at * 1000)}
                        />
                      ) : (
                        'Never'
                      )}
                    </TableCell>
                    <TableCell className="truncate">
                      {token.last_used_at ? (
                        <TimestampWithTooltip
                          date={new Date(token.last_used_at * 1000)}
                        />
                      ) : (
                        'Never'
                      )}
                    </TableCell>
                    <TableCell className="truncate">
                      {!token.expires_at ? (
                        'Never'
                      ) : new Date(token.expires_at * 1000) < new Date() ? (
                        <span className="text-red-600">Expired</span>
                      ) : (
                        <TimestampWithTooltip
                          date={new Date(token.expires_at * 1000)}
                        />
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center space-x-2">
                        {/* Show rotate button only if user owns the token or is admin */}
                        {(userRoleCache?.role === 'admin' ||
                          token.creator_user_hash === userRoleCache?.id) && (
                          <CustomTooltip
                            content={`Rotate token`}
                            className="capitalize text-sm text-muted-foreground"
                          >
                            <button
                              onClick={() => {
                                checkPermissionAndAct(
                                  'cannot rotate service account tokens',
                                  () => {
                                    setTokenToRotate(token);
                                    setShowRotateDialog(true);
                                  }
                                );
                              }}
                              className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center"
                            >
                              <RotateCwIcon className="h-4 w-4" />
                            </button>
                          </CustomTooltip>
                        )}
                        {/* Show delete button only if user owns the token or is admin */}
                        {(userRoleCache?.role === 'admin' ||
                          token.creator_user_hash === userRoleCache?.id) && (
                          <Tooltip
                            content={`Delete ${token.token_name}`}
                            className="capitalize text-sm text-muted-foreground"
                          >
                            <button
                              onClick={() => {
                                checkPermissionAndAct(
                                  'cannot delete service account tokens',
                                  () => {
                                    setTokenToDelete(token);
                                    setShowDeleteDialog(true);
                                  }
                                );
                              }}
                              className="text-red-600 hover:text-red-800 font-medium inline-flex items-center"
                            >
                              <Trash2Icon className="h-4 w-4" />
                            </button>
                          </Tooltip>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </>
      )}

      {/* Create Service Account Dialog */}
      <Dialog
        open={showCreateDialog}
        onOpenChange={(open) => {
          setShowCreateDialog(open);
          if (!open) {
            setCreatedTokenInDialog(null);
            setCreateError(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Create Service Account</DialogTitle>
            <DialogDescription>
              Create a new service account with an API token for programmatic
              access to SkyPilot.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-4">
            {createdTokenInDialog ? (
              /* Token Created Successfully - Show Token */
              <>
                <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
                  <div className="flex items-center mb-3">
                    <h4 className="text-sm font-medium text-green-900">
                      ⚠️ Service account created successfully - save this token
                      now!
                    </h4>
                    <CustomTooltip
                      content={copySuccess ? 'Copied!' : 'Copy token'}
                      className="text-muted-foreground"
                    >
                      <button
                        onClick={() => copyToClipboard(createdTokenInDialog)}
                        className="flex items-center text-green-600 hover:text-green-800 transition-colors duration-200 p-1 ml-2"
                      >
                        {copySuccess ? (
                          <CheckIcon className="w-4 h-4" />
                        ) : (
                          <CopyIcon className="w-4 h-4" />
                        )}
                      </button>
                    </CustomTooltip>
                  </div>
                  <p className="text-sm text-green-700 mb-3">
                    This service account token will not be shown again. Please
                    copy and store it securely.
                  </p>
                  <div className="bg-white border border-green-300 rounded-md p-3">
                    <code className="text-sm text-gray-800 font-mono break-all block">
                      {createdTokenInDialog}
                    </code>
                  </div>
                </div>
              </>
            ) : (
              /* Token Creation Form */
              <>
                <div className="grid gap-2">
                  <label className="text-sm font-medium text-gray-700">
                    Service Account Name
                  </label>
                  <input
                    className="border rounded px-3 py-2 w-full"
                    placeholder="e.g., ci-pipeline, monitoring-system"
                    value={newToken.token_name}
                    onChange={(e) =>
                      setNewToken({ ...newToken, token_name: e.target.value })
                    }
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-sm font-medium text-gray-700">
                    Expiration (days)
                  </label>
                  <input
                    type="number"
                    className="border rounded px-3 py-2 w-full"
                    placeholder="e.g., 30"
                    min="0"
                    max="365"
                    value={newToken.expires_in_days ?? ''}
                    onChange={(e) =>
                      setNewToken({
                        ...newToken,
                        expires_in_days: e.target.value
                          ? parseInt(e.target.value)
                          : null,
                      })
                    }
                  />
                  <p className="text-xs text-gray-500">
                    Leave empty or enter 0 to never expire. Maximum 365 days.
                  </p>
                </div>
              </>
            )}
          </div>
          <DialogFooter>
            {createdTokenInDialog ? (
              <button
                className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-sky-600 text-white hover:bg-sky-700 h-10 px-4 py-2"
                onClick={() => {
                  setShowCreateDialog(false);
                  setCreatedTokenInDialog(null);
                }}
              >
                Close
              </button>
            ) : (
              <>
                <button
                  className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-10 px-4 py-2"
                  onClick={() => {
                    setShowCreateDialog(false);
                    setCreatedTokenInDialog(null);
                  }}
                  disabled={creating}
                >
                  Cancel
                </button>
                <button
                  className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-sky-600 text-white hover:bg-sky-700 h-10 px-4 py-2"
                  onClick={handleCreateToken}
                  disabled={creating || !newToken.token_name.trim()}
                >
                  {creating ? 'Creating...' : 'Create Token'}
                </button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Token Dialog */}
      <Dialog
        open={showDeleteDialog}
        onOpenChange={(open) => {
          setShowDeleteDialog(open);
          if (!open) {
            setTokenToDelete(null);
            setCreateError(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete Service Account Token</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete the service account &quot;
              {tokenToDelete?.token_name}&quot;
              {tokenToDelete?.creator_user_hash !== userRoleCache?.id &&
              userRoleCache?.role === 'admin'
                ? ` owned by ${tokenToDelete?.creator_name}`
                : ''}
              ? This action cannot be undone and will immediately revoke access
              for any systems using this token.
            </DialogDescription>
          </DialogHeader>

          <DialogFooter>
            <button
              className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-10 px-4 py-2"
              onClick={() => {
                setShowDeleteDialog(false);
                setTokenToDelete(null);
              }}
              disabled={deleting}
            >
              Cancel
            </button>
            <button
              className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-red-600 text-white hover:bg-red-700 h-10 px-4 py-2"
              onClick={handleDeleteToken}
              disabled={deleting}
            >
              {deleting ? 'Deleting...' : 'Delete Token'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Rotate Token Dialog */}
      <Dialog
        open={showRotateDialog}
        onOpenChange={(open) => {
          setShowRotateDialog(open);
          if (!open) {
            setTokenToRotate(null);
            setRotateExpiration('');
            setRotatedTokenInDialog(null);
            setCreateError(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Rotate Service Account Token</DialogTitle>
            <DialogDescription>
              Rotate the service account token &quot;{tokenToRotate?.token_name}
              &quot;
              {tokenToRotate?.creator_user_hash !== userRoleCache?.id &&
              userRoleCache?.role === 'admin'
                ? ` owned by ${tokenToRotate?.creator_name}`
                : ''}
              . This will generate a new token value and invalidate the current
              one.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-4">
            {rotatedTokenInDialog ? (
              /* Token Rotated Successfully - Show Token */
              <>
                <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
                  <div className="flex items-center mb-3">
                    <h4 className="text-sm font-medium text-green-900">
                      🔄 Service account token rotated successfully - save this
                      new token now!
                    </h4>
                    <CustomTooltip
                      content={copySuccess ? 'Copied!' : 'Copy token'}
                      className="text-muted-foreground"
                    >
                      <button
                        onClick={() => copyToClipboard(rotatedTokenInDialog)}
                        className="flex items-center text-green-600 hover:text-green-800 transition-colors duration-200 p-1 ml-2"
                      >
                        {copySuccess ? (
                          <CheckIcon className="w-4 h-4" />
                        ) : (
                          <CopyIcon className="w-4 h-4" />
                        )}
                      </button>
                    </CustomTooltip>
                  </div>
                  <p className="text-sm text-green-700 mb-3">
                    This new token replaces the old one. Please copy and store
                    it securely. The old token is now invalid.
                  </p>
                  <div className="bg-white border border-green-300 rounded-md p-3">
                    <code className="text-sm text-gray-800 font-mono break-all block">
                      {rotatedTokenInDialog}
                    </code>
                  </div>
                </div>
              </>
            ) : (
              /* Token Rotation Form */
              <>
                <div className="grid gap-2">
                  <label className="text-sm font-medium text-gray-700">
                    New Expiration (days)
                  </label>
                  <input
                    type="number"
                    className="border rounded px-3 py-2 w-full"
                    placeholder="Leave empty to preserve current expiration"
                    min="0"
                    max="365"
                    value={rotateExpiration}
                    onChange={(e) => setRotateExpiration(e.target.value)}
                  />
                  <p className="text-xs text-gray-500">
                    Leave empty to preserve current expiration. Enter number of
                    days for new expiration, or enter 0 to set to never expire.
                    Maximum 365 days.
                  </p>
                </div>
                <div className="p-3 bg-amber-50 border border-amber-200 rounded">
                  <p className="text-sm text-amber-700">
                    ⚠️ Any systems using the current token will need to be
                    updated with the new token.
                  </p>
                </div>
              </>
            )}
          </div>
          <DialogFooter>
            {rotatedTokenInDialog ? (
              <button
                className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-green-600 text-white hover:bg-green-700 h-10 px-4 py-2"
                onClick={() => {
                  setShowRotateDialog(false);
                  setTokenToRotate(null);
                  setRotateExpiration('');
                  setRotatedTokenInDialog(null);
                }}
              >
                Close
              </button>
            ) : (
              <>
                <button
                  className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-10 px-4 py-2"
                  onClick={() => {
                    setShowRotateDialog(false);
                    setTokenToRotate(null);
                    setRotateExpiration('');
                    setRotatedTokenInDialog(null);
                  }}
                  disabled={rotating}
                >
                  Cancel
                </button>
                <button
                  className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-sky-600 text-white hover:bg-sky-700 h-10 px-4 py-2"
                  onClick={handleRotateToken}
                  disabled={rotating}
                >
                  {rotating ? 'Rotating...' : 'Rotate Token'}
                </button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
