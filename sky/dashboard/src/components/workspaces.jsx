'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/router';
import {
  getWorkspaces,
  getEnabledClouds,
  deleteWorkspace,
} from '@/data/connectors/workspaces';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardFooter,
} from '@/components/ui/card';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { CircularProgress } from '@mui/material';
import yaml from 'js-yaml';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  ServerIcon,
  BriefcaseIcon,
  BookDocIcon,
  TickIcon,
} from '@/components/elements/icons';
import { ErrorDisplay } from '@/components/elements/ErrorDisplay';
import { RotateCwIcon, PlusIcon, Trash2Icon, EditIcon } from 'lucide-react';
import { LastUpdatedTimestamp } from '@/components/utils';
import { useMobile } from '@/hooks/useMobile';
import { statusGroups } from './jobs';
import dashboardCache from '@/lib/cache';
import { REFRESH_INTERVALS } from '@/lib/config';
import cachePreloader from '@/lib/cache-preloader';
import { apiClient } from '@/data/connectors/client';
import { sortData } from '@/data/utils';
import {
  CLOUD_CANONICALIZATIONS,
  CLUSTER_NOT_UP_ERROR,
} from '@/data/connectors/constants';
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
import Link from 'next/link';

// Workspace-aware API functions - use cached global data and filter by workspace
// This avoids making separate API calls per workspace
export async function getWorkspaceClusters(workspaceName) {
  try {
    // Use cached global clusters data and filter by workspace
    const allClusters = await dashboardCache.get(getClusters);

    // Filter clusters to only include those that belong to the requested workspace
    const filteredClusters = (allClusters || []).filter(
      (cluster) => cluster.workspace === workspaceName
    );
    return filteredClusters;
  } catch (error) {
    const msg = `Error fetching clusters for workspace ${workspaceName}: ${error}`;
    console.error(msg);
    throw new Error(msg);
  }
}

export async function getWorkspaceManagedJobs(workspaceName) {
  try {
    // Use cached global managed jobs data and filter by workspace
    // This avoids making separate API calls per workspace
    const allJobsData = await dashboardCache.get(getManagedJobs, [
      { allUsers: true, skipFinished: true },
    ]);

    const allJobs = allJobsData?.jobs || [];

    // Filter jobs to only include those that belong to the requested workspace
    const filteredJobs = allJobs.filter(
      (job) => job.workspace === workspaceName
    );

    return { jobs: filteredJobs };
  } catch (error) {
    const msg = `Error fetching managed jobs for workspace ${workspaceName}: ${error}`;
    console.error(msg);
    throw new Error(msg);
  }
}

// Workspace configuration description component
const WorkspaceConfigDescription = ({ workspaceName, config }) => {
  if (!config) return null;

  const isDefault = workspaceName === 'default';
  const isEmptyConfig = Object.keys(config).length === 0;

  if (isDefault && isEmptyConfig) {
    return (
      <div className="text-sm text-gray-500 mb-3 italic p-3 bg-sky-50 rounded border border-sky-200">
        Workspace &apos;default&apos; can use all accessible infrastructure.
      </div>
    );
  }

  const enabledDescriptions = [];
  const disabledClouds = [];

  Object.entries(config).forEach(([cloud, cloudConfig]) => {
    const cloudNameUpper = cloud.toUpperCase();

    if (cloudConfig?.disabled === true) {
      disabledClouds.push(cloudNameUpper);
    } else if (cloudConfig && Object.keys(cloudConfig).length > 0) {
      let detail = '';
      if (cloud.toLowerCase() === 'gcp' && cloudConfig.project_id) {
        detail = ` (Project ID: ${cloudConfig.project_id})`;
      } else if (cloud.toLowerCase() === 'aws' && cloudConfig.region) {
        detail = ` (Region: ${cloudConfig.region})`;
      }
      enabledDescriptions.push(
        <span key={`${cloud}-enabled`} className="block">
          {cloudNameUpper}
          {detail} is enabled.
        </span>
      );
    } else {
      enabledDescriptions.push(
        <span key={`${cloud}-default-enabled`} className="block">
          {cloudNameUpper} is enabled (using default settings).
        </span>
      );
    }
  });

  const finalDescriptions = [];
  if (disabledClouds.length > 0) {
    const disabledString = disabledClouds.join(' and ');
    finalDescriptions.push(
      <span key="disabled-clouds" className="block">
        {disabledString} {disabledClouds.length === 1 ? 'is' : 'are'} explicitly
        disabled.
      </span>
    );
  }
  finalDescriptions.push(...enabledDescriptions);

  if (finalDescriptions.length > 0) {
    return (
      <div className="text-sm text-gray-700 mb-3 p-3 bg-sky-50 rounded border border-sky-200">
        {finalDescriptions}
        <p className="mt-2 text-gray-500">
          Other accessible infrastructure are enabled. See{' '}
          <code className="text-sky-blue">Enabled Infra</code>.
        </p>
      </div>
    );
  }

  if (!isDefault && isEmptyConfig) {
    return (
      <div className="text-sm text-gray-500 mb-3 italic p-3 bg-sky-50 rounded border border-sky-200">
        This workspace has no specific cloud resource configurations and can use
        all accessible infrastructure.
      </div>
    );
  }
  return null;
};

// Workspace badge component for private/public status
const WorkspaceBadge = ({ isPrivate }) => {
  if (isPrivate) {
    return (
      <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-700 border border-gray-300">
        Private
      </span>
    );
  }
  return (
    <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-green-100 text-green-700 border border-green-300">
      Public
    </span>
  );
};

// Statistics summary component
const StatsSummary = ({
  workspaceCount,
  runningClusters,
  totalClusters,
  managedJobs,
  router,
}) => (
  <div className="bg-sky-50 p-4 rounded-lg shadow mb-6">
    <div className="flex flex-col sm:flex-row justify-around items-center">
      <div className="p-2">
        <div className="flex items-center">
          <BookDocIcon className="w-5 h-5 mr-2 text-sky-600" />
          <span className="text-sm text-gray-600">Workspaces:</span>
          <span className="ml-1 text-xl font-semibold text-sky-700">
            {workspaceCount}
          </span>
        </div>
      </div>
      <div className="p-2">
        <div className="flex items-center">
          <ServerIcon className="w-5 h-5 mr-2 text-sky-600" />
          <span className="text-sm text-gray-600">
            Clusters (Running / Total):
          </span>
          <button
            onClick={() => router.push('/clusters')}
            className="ml-1 text-xl font-semibold text-blue-600 hover:text-blue-800 hover:underline cursor-pointer"
          >
            {runningClusters} / {totalClusters}
          </button>
        </div>
      </div>
      <div className="p-2">
        <div className="flex items-center">
          <BriefcaseIcon className="w-5 h-5 mr-2 text-sky-600" />
          <span className="text-sm text-gray-600">Managed Jobs:</span>
          <button
            onClick={() => router.push('/jobs')}
            className="ml-1 text-xl font-semibold text-blue-600 hover:text-blue-800 hover:underline cursor-pointer"
          >
            {managedJobs}
          </button>
        </div>
      </div>
    </div>
  </div>
);

const REFRESH_INTERVAL = REFRESH_INTERVALS.REFRESH_INTERVAL;

export function Workspaces() {
  const [workspaceDetails, setWorkspaceDetails] = useState([]);
  const [globalStats, setGlobalStats] = useState({
    runningClusters: 0,
    totalClusters: 0,
    managedJobs: 0,
  });
  const [clustersLoading, setClustersLoading] = useState(true);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [rawWorkspacesData, setRawWorkspacesData] = useState(null);
  const [lastFetchedTime, setLastFetchedTime] = useState(null);

  // Track if this is the initial load (controls panel-level vs cell-level spinners)
  const [isInitialLoad, setIsInitialLoad] = useState(true);

  // Sorting state
  const [sortConfig, setSortConfig] = useState({
    key: 'name',
    direction: 'asc',
  });

  // Search state
  const [searchQuery, setSearchQuery] = useState('');

  // Modal states
  const [isAllWorkspacesModalOpen, setIsAllWorkspacesModalOpen] =
    useState(false);

  // Delete confirmation states
  const [deleteState, setDeleteState] = useState({
    confirmOpen: false,
    workspaceToDelete: null,
    deleting: false,
    error: null,
  });

  // Permission denial dialog state
  const [permissionDenialState, setPermissionDenialState] = useState({
    open: false,
    message: '',
    userName: '',
  });

  // User role cache
  const [userRoleCache, setUserRoleCache] = useState(null);
  const [roleLoading, setRoleLoading] = useState(false);

  // Top-level error and success states
  const [topLevelError, setTopLevelError] = useState(null);
  const [topLevelSuccess, setTopLevelSuccess] = useState(null);

  const router = useRouter();
  const isMobile = useMobile();

  // Function to get user role with caching
  const getUserRole = async () => {
    // Return cached result if available and less than 5 minutes old
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
        timestamp: Date.now(),
      };
      setUserRoleCache(roleData);
      setRoleLoading(false);
      return roleData;
    } catch (error) {
      setRoleLoading(false);
      throw error;
    }
  };

  // Function to handle permission check with smooth UX
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

  // Fetch clusters independently and update state progressively
  const fetchClustersData = useCallback(
    async (workspaceNames, enabledCloudsMap) => {
      try {
        const allClusters = await dashboardCache.get(getClusters);

        // Calculate per-workspace cluster stats
        const workspaceClusterStats = {};
        let totalRunningClusters = 0;

        workspaceNames.forEach((wsName) => {
          workspaceClusterStats[wsName] = {
            totalClusterCount: 0,
            runningClusterCount: 0,
          };
        });

        (allClusters || []).forEach((cluster) => {
          const wsName = cluster.workspace || 'default';
          if (!workspaceClusterStats[wsName]) {
            workspaceClusterStats[wsName] = {
              totalClusterCount: 0,
              runningClusterCount: 0,
            };
          }
          workspaceClusterStats[wsName].totalClusterCount++;
          if (cluster.status === 'RUNNING' || cluster.status === 'LAUNCHING') {
            workspaceClusterStats[wsName].runningClusterCount++;
            totalRunningClusters++;
          }
        });

        // Update workspaceDetails with cluster data
        setWorkspaceDetails((prev) => {
          return prev.map((ws) => ({
            ...ws,
            totalClusterCount:
              workspaceClusterStats[ws.name]?.totalClusterCount || 0,
            runningClusterCount:
              workspaceClusterStats[ws.name]?.runningClusterCount || 0,
          }));
        });

        // Update global stats for clusters
        setGlobalStats((prev) => ({
          ...prev,
          runningClusters: totalRunningClusters,
          totalClusters: (allClusters || []).length,
        }));
      } catch (error) {
        console.error('Error fetching clusters:', error);
      } finally {
        setClustersLoading(false);
      }
    },
    []
  );

  // Fetch jobs independently and update state progressively
  const fetchJobsData = useCallback(async (workspaceNames) => {
    try {
      const allJobsData = await dashboardCache.get(getManagedJobs, [
        { allUsers: true, skipFinished: true },
      ]);
      const jobs = allJobsData?.jobs || [];

      // Calculate per-workspace job stats
      const workspaceJobStats = {};
      const activeJobStatuses = new Set(statusGroups.active);
      let activeGlobalManagedJobs = 0;

      workspaceNames.forEach((wsName) => {
        workspaceJobStats[wsName] = { managedJobsCount: 0 };
      });

      jobs.forEach((job) => {
        const wsName = job.workspace || 'default';
        if (!workspaceJobStats[wsName]) {
          workspaceJobStats[wsName] = { managedJobsCount: 0 };
        }
        if (activeJobStatuses.has(job.status)) {
          workspaceJobStats[wsName].managedJobsCount++;
          activeGlobalManagedJobs++;
        }
      });

      // Update workspaceDetails with job data
      setWorkspaceDetails((prev) => {
        return prev.map((ws) => ({
          ...ws,
          managedJobsCount: workspaceJobStats[ws.name]?.managedJobsCount || 0,
        }));
      });

      // Update global stats for jobs
      setGlobalStats((prev) => ({
        ...prev,
        managedJobs: activeGlobalManagedJobs,
      }));
    } catch (error) {
      console.error('Error fetching jobs:', error);
    } finally {
      setJobsLoading(false);
    }
  }, []);

  const fetchData = useCallback(
    async (options = { showLoadingIndicators: true }) => {
      const { showLoadingIndicators = true } = options;

      if (showLoadingIndicators) {
        setClustersLoading(true);
        setJobsLoading(true);
      }

      try {
        // First, get the list of workspaces the user has access to
        const fetchedWorkspacesConfig = await dashboardCache.get(getWorkspaces);
        setRawWorkspacesData(fetchedWorkspacesConfig);
        const configuredWorkspaceNames = Object.keys(fetchedWorkspacesConfig);

        // Fetch enabledClouds for all workspaces in parallel
        const enabledCloudsPromises = configuredWorkspaceNames.map(
          async (wsName) => {
            try {
              const enabledClouds = await dashboardCache.get(getEnabledClouds, [
                wsName,
              ]);
              return { wsName, enabledClouds };
            } catch (error) {
              console.error(
                `Error fetching enabled clouds for ${wsName}:`,
                error
              );
              return { wsName, enabledClouds: [] };
            }
          }
        );

        const enabledCloudsResults = await Promise.all(enabledCloudsPromises);
        const enabledCloudsMap = {};
        enabledCloudsResults.forEach(({ wsName, enabledClouds }) => {
          enabledCloudsMap[wsName] = enabledClouds;
        });

        // Initialize workspace details with zeros - UI will show spinners for counts
        const initialWorkspaceDetails = configuredWorkspaceNames
          .map((wsName) => ({
            name: wsName,
            totalClusterCount: 0,
            runningClusterCount: 0,
            managedJobsCount: 0,
            clouds: Array.isArray(enabledCloudsMap[wsName])
              ? enabledCloudsMap[wsName]
              : [],
          }))
          .sort((a, b) => a.name.localeCompare(b.name));

        setWorkspaceDetails(initialWorkspaceDetails);

        // Mark initial loading as complete so the table renders
        if (isInitialLoad && showLoadingIndicators) {
          setIsInitialLoad(false);
        }

        // Launch clusters and jobs fetches in parallel
        // Each function updates its data immediately when done and sets its loading state to false
        const clustersPromise = fetchClustersData(
          configuredWorkspaceNames,
          enabledCloudsMap
        );
        const jobsPromise = fetchJobsData(configuredWorkspaceNames);

        // Wait for both to complete (errors are handled inside each function)
        await Promise.all([clustersPromise, jobsPromise]);
      } catch (error) {
        console.error('Error fetching workspace data:', error);
        // Don't clear data on error during refresh - keep showing stale data
        if (isInitialLoad) {
          setWorkspaceDetails([]);
          setGlobalStats({
            runningClusters: 0,
            totalClusters: 0,
            managedJobs: 0,
          });
        }
        if (showLoadingIndicators) {
          setClustersLoading(false);
          setJobsLoading(false);
        }
        if (isInitialLoad && showLoadingIndicators) {
          setIsInitialLoad(false);
        }
      }
    },
    [isInitialLoad, fetchClustersData, fetchJobsData]
  );

  useEffect(() => {
    const initializeData = async () => {
      // Trigger cache preloading for workspaces page and background preload other pages
      await cachePreloader.preloadForPage('workspaces');

      await fetchData({ showLoadingIndicators: true });
      setLastFetchedTime(new Date());
    };

    initializeData();

    // Set up refresh interval
    const interval = setInterval(() => {
      if (window.document.visibilityState === 'visible') {
        fetchData({ showLoadingIndicators: false });
      }
    }, REFRESH_INTERVALS.REFRESH_INTERVAL);

    return () => clearInterval(interval);
  }, [fetchData]);

  const handleRefresh = useCallback(async () => {
    // Set loading states immediately for responsive UI
    setClustersLoading(true);
    setJobsLoading(true);

    // Invalidate cache to ensure fresh data is fetched
    dashboardCache.invalidate(getWorkspaces);
    dashboardCache.invalidateFunction(getEnabledClouds); // This function has arguments

    // Invalidate cluster and job caches
    dashboardCache.invalidate(getClusters);
    dashboardCache.invalidateFunction(getManagedJobs);

    try {
      await apiClient.fetch('/check', {}, 'POST');
      await fetchData({ showLoadingIndicators: false });
      setLastFetchedTime(new Date());
    } catch (error) {
      console.error('Error during sky check refresh:', error);
    } finally {
      setClustersLoading(false);
      setJobsLoading(false);
    }
  }, [fetchData]);

  // Intercept Cmd+R / Ctrl+R to trigger in-app refresh instead of browser reload
  useEffect(() => {
    const handleKeyDown = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'r') {
        event.preventDefault();
        handleRefresh();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleRefresh]);

  // Sorting functionality
  const handleSort = (key) => {
    let direction = 'asc';
    if (sortConfig.key === key && sortConfig.direction === 'asc') {
      direction = 'desc';
    }
    setSortConfig({ key, direction });
  };

  const getSortDirection = (key) => {
    if (sortConfig.key === key) {
      return sortConfig.direction === 'asc' ? ' ↑' : ' ↓';
    }
    return '';
  };

  const sortedWorkspaces = React.useMemo(() => {
    if (!workspaceDetails) return [];

    // First apply search filter
    let filtered = workspaceDetails;
    if (searchQuery && searchQuery.trim() !== '') {
      const searchLower = searchQuery.toLowerCase().trim();
      filtered = workspaceDetails.filter((workspace) => {
        // Check workspace name
        if (workspace.name.toLowerCase().includes(searchLower)) {
          return true;
        }

        // Check infrastructure clouds (both original and canonical names)
        if (
          workspace.clouds.some((cloud) => {
            const canonicalCloudName =
              CLOUD_CANONICALIZATIONS[cloud.toLowerCase()] || cloud;
            return (
              cloud.toLowerCase().includes(searchLower) ||
              canonicalCloudName.toLowerCase().includes(searchLower)
            );
          })
        ) {
          return true;
        }

        // Check public/private status
        const workspaceConfig = rawWorkspacesData?.[workspace.name] || {};
        const isPrivate = workspaceConfig.private === true;
        const status = isPrivate ? 'private' : 'public';
        if (status.includes(searchLower)) {
          return true;
        }

        return false;
      });
    }

    // Then apply sorting
    return sortData(filtered, sortConfig.key, sortConfig.direction);
  }, [workspaceDetails, sortConfig, searchQuery, rawWorkspacesData]);

  const handleDeleteWorkspace = (workspaceName) => {
    checkPermissionAndAct('cannot delete workspace', () => {
      setDeleteState({
        confirmOpen: true,
        workspaceToDelete: workspaceName,
        deleting: false,
        error: null,
      });
    });
  };

  const handleConfirmDelete = async () => {
    if (!deleteState.workspaceToDelete) return;

    setDeleteState((prev) => ({ ...prev, deleting: true, error: null }));
    try {
      await deleteWorkspace(deleteState.workspaceToDelete);

      // Show success message at top level
      setTopLevelSuccess(
        `Workspace "${deleteState.workspaceToDelete}" deleted successfully!`
      );

      setDeleteState({
        confirmOpen: false,
        workspaceToDelete: null,
        deleting: false,
        error: null,
      });

      // Invalidate cache to ensure fresh data is fetched (same as manual refresh)
      dashboardCache.invalidate(getWorkspaces);
      dashboardCache.invalidate(getClusters);
      dashboardCache.invalidateFunction(getManagedJobs);

      await fetchData({ showLoadingIndicators: true });
    } catch (error) {
      console.error('Error deleting workspace:', error);

      // Keep dialog open and show error at top level for better UX
      setDeleteState((prev) => ({
        ...prev,
        deleting: false,
        error: null,
      }));
      setTopLevelError(error);
    }
  };

  const handleCancelDelete = () => {
    setDeleteState({
      confirmOpen: false,
      workspaceToDelete: null,
      deleting: false,
      error: null,
    });
  };

  const handleCreateWorkspace = () => {
    checkPermissionAndAct('cannot create workspace', () => {
      router.push('/workspace/new');
    });
  };

  const handleEditWorkspace = (workspaceName) => {
    checkPermissionAndAct('cannot edit workspace', () => {
      router.push(`/workspaces/${workspaceName}`);
    });
  };

  const preStyle = {
    backgroundColor: '#f5f5f5',
    padding: '16px',
    borderRadius: '8px',
    overflowX: 'auto',
    whiteSpace: 'pre',
    wordBreak: 'normal',
  };

  // Only show full-page loading spinner during initial load
  if (isInitialLoad && workspaceDetails.length === 0) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress />
        <span className="ml-2 text-gray-500">Loading workspaces...</span>
      </div>
    );
  }

  return (
    <div>
      {/* Error/Success messages positioned at top right, below navigation bar */}
      <div className="fixed top-20 right-4 z-[9999] max-w-md">
        {topLevelSuccess && (
          <div className="bg-green-50 border border-green-200 rounded p-4 mb-4">
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
                  <p className="text-sm font-medium text-green-800">
                    {topLevelSuccess}
                  </p>
                </div>
              </div>
              <div className="ml-auto pl-3">
                <button
                  type="button"
                  onClick={() => setTopLevelSuccess(null)}
                  className="inline-flex rounded-md bg-green-50 p-1.5 text-green-500 hover:bg-green-100"
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
          </div>
        )}
        <ErrorDisplay
          error={topLevelError}
          title="Error"
          onDismiss={() => setTopLevelError(null)}
        />
      </div>

      {/* Header */}
      <div className="flex items-center justify-between mb-2 h-5">
        <div className="text-base flex items-center">
          <span className="text-sky-blue leading-none">Workspaces</span>
        </div>
        <div className="flex items-center">
          {(clustersLoading || jobsLoading) && (
            <div className="flex items-center mr-2">
              <CircularProgress size={15} className="mt-0" />
              <span className="ml-2 text-gray-500 text-xs">Loading...</span>
            </div>
          )}
          {!clustersLoading && !jobsLoading && lastFetchedTime && (
            <LastUpdatedTimestamp
              timestamp={lastFetchedTime}
              className="mr-2"
            />
          )}
          <button
            onClick={handleRefresh}
            disabled={clustersLoading || jobsLoading}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            {!isMobile && <span>Refresh</span>}
          </button>
        </div>
      </div>

      {/* Search and Create Workspace Row */}
      <div className="flex items-center justify-between mb-4">
        <div className="relative flex-1 max-w-md">
          <input
            type="text"
            placeholder="Filter workspaces"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-8 w-full px-3 pr-8 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-sky-500 focus:border-sky-500 outline-none"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
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

        {/* Create Workspace Button */}
        <button
          onClick={handleCreateWorkspace}
          disabled={roleLoading}
          className="ml-4 bg-sky-600 hover:bg-sky-700 text-white flex items-center rounded-md px-3 py-1 text-sm font-medium transition-colors duration-200"
          title="Create Workspace"
        >
          {roleLoading ? (
            <>
              <CircularProgress size={12} className="mr-2" />
              <span>Create Workspace</span>
            </>
          ) : (
            <>
              <PlusIcon className="h-4 w-4 mr-2" />
              Create Workspace
            </>
          )}
        </button>
      </div>

      {/* Workspaces Table */}
      {workspaceDetails.length === 0 && !isInitialLoad ? (
        <div className="text-center py-10">
          <p className="text-lg text-gray-600">No workspaces found.</p>
          <p className="text-sm text-gray-500 mt-2">
            Create a cluster to see its workspace here.
          </p>
        </div>
      ) : (
        <Card>
          <div className="overflow-x-auto rounded-lg">
            <Table className="min-w-full">
              <TableHeader>
                <TableRow>
                  <TableHead
                    className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort('name')}
                  >
                    Workspace{getSortDirection('name')}
                  </TableHead>
                  <TableHead
                    className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort('runningClusterCount')}
                  >
                    Running Clusters {getSortDirection('runningClusterCount')}
                  </TableHead>
                  <TableHead
                    className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort('managedJobsCount')}
                  >
                    Jobs{getSortDirection('managedJobsCount')}
                  </TableHead>
                  <TableHead className="whitespace-nowrap">
                    Enabled infra
                  </TableHead>
                  <TableHead className="whitespace-nowrap">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isInitialLoad && sortedWorkspaces.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={5}
                      className="text-center py-6 text-gray-500"
                    >
                      <div className="flex justify-center items-center">
                        <CircularProgress size={20} className="mr-2" />
                        <span>Loading...</span>
                      </div>
                    </TableCell>
                  </TableRow>
                ) : sortedWorkspaces.length > 0 ? (
                  sortedWorkspaces.map((workspace) => {
                    // Get the workspace configuration to check if it's private
                    const workspaceConfig =
                      rawWorkspacesData?.[workspace.name] || {};
                    const isPrivate = workspaceConfig.private === true;

                    return (
                      <TableRow
                        key={workspace.name}
                        className="hover:bg-gray-50"
                      >
                        <TableCell className="">
                          <button
                            onClick={() => handleEditWorkspace(workspace.name)}
                            disabled={roleLoading}
                            className="text-blue-600 hover:text-blue-600 hover:underline text-left"
                          >
                            {workspace.name}
                          </button>
                          <span className="ml-2">
                            <WorkspaceBadge isPrivate={isPrivate} />
                          </span>
                        </TableCell>
                        <TableCell>
                          <button
                            onClick={() => {
                              router.push({
                                pathname: '/clusters',
                                query: { workspace: workspace.name },
                              });
                            }}
                            className="text-gray-700 hover:text-blue-600 hover:underline"
                          >
                            <span className="inline-flex items-center px-2 py-0.5 bg-gray-100 text-gray-700 rounded text-sm">
                              {clustersLoading ? (
                                <CircularProgress size={12} />
                              ) : (
                                workspace.runningClusterCount
                              )}
                            </span>
                          </button>
                        </TableCell>
                        <TableCell>
                          <button
                            onClick={() => {
                              router.push({
                                pathname: '/jobs',
                                query: { workspace: workspace.name },
                              });
                            }}
                            className="text-gray-700 hover:text-blue-600 hover:underline"
                          >
                            <span className="inline-flex items-center px-2 py-0.5 bg-gray-100 text-gray-700 rounded text-sm">
                              {jobsLoading ? (
                                <CircularProgress size={12} />
                              ) : (
                                workspace.managedJobsCount
                              )}
                            </span>
                          </button>
                        </TableCell>
                        <TableCell>
                          {workspace.clouds.length > 0 ? (
                            [...workspace.clouds].sort().map((cloud, index) => {
                              const canonicalCloudName =
                                CLOUD_CANONICALIZATIONS[cloud.toLowerCase()] ||
                                cloud;
                              return (
                                <span key={cloud}>
                                  <Link
                                    href="/infra"
                                    className="inline-flex items-center px-2 py-1 rounded text-sm bg-sky-100 text-sky-800 hover:bg-sky-200 hover:text-sky-900 transition-colors duration-200"
                                  >
                                    {canonicalCloudName}
                                  </Link>
                                  {index < workspace.clouds.length - 1 && ' '}
                                </span>
                              );
                            })
                          ) : (
                            <span className="text-gray-500 text-sm">-</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleEditWorkspace(workspace.name)}
                            disabled={roleLoading}
                            className="text-gray-600 hover:text-gray-800 mr-1"
                          >
                            <EditIcon className="w-4 h-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              handleDeleteWorkspace(workspace.name)
                            }
                            disabled={
                              workspace.name === 'default' || roleLoading
                            }
                            title={
                              workspace.name === 'default'
                                ? 'Cannot delete default workspace'
                                : 'Delete workspace'
                            }
                            className="text-red-600 hover:text-red-700 hover:bg-red-50"
                          >
                            <Trash2Icon className="w-4 h-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })
                ) : (
                  <TableRow>
                    <TableCell
                      colSpan={5}
                      className="text-center py-6 text-gray-500"
                    >
                      No workspaces found
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </Card>
      )}

      {/* All Workspaces Config Modal */}
      {rawWorkspacesData && (
        <Dialog
          open={isAllWorkspacesModalOpen}
          onOpenChange={setIsAllWorkspacesModalOpen}
        >
          <DialogContent className="sm:max-w-md md:max-w-lg lg:max-w-xl xl:max-w-2xl w-full max-h-[90vh] flex flex-col">
            <DialogHeader>
              <DialogTitle className="pr-10">
                All Workspaces Configuration
              </DialogTitle>
            </DialogHeader>
            <div className="flex-grow overflow-y-auto py-4">
              <pre style={preStyle}>
                {yaml.dump(rawWorkspacesData, { indent: 2 })}
              </pre>
            </div>
          </DialogContent>
        </Dialog>
      )}

      {/* Permission Denial Dialog */}
      <Dialog
        open={permissionDenialState.open}
        onOpenChange={(open) => {
          setPermissionDenialState((prev) => ({ ...prev, open }));
          if (!open) {
            setTopLevelError(null);
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

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteState.confirmOpen}
        onOpenChange={(open) => {
          if (open) return;
          handleCancelDelete();
          setTopLevelError(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete Workspace</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete workspace &quot;
              {deleteState.workspaceToDelete}&quot;? This action cannot be
              undone.
            </DialogDescription>
          </DialogHeader>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={handleCancelDelete}
              disabled={deleteState.deleting}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deleteState.deleting}
            >
              {deleteState.deleting ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
