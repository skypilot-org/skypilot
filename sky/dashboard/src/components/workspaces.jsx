'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
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
import { RotateCwIcon } from 'lucide-react';
import { useMobile } from '@/hooks/useMobile';
import { statusGroups } from './jobs';
import dashboardCache from '@/lib/cache';
import { REFRESH_INTERVALS } from '@/lib/config';
import cachePreloader from '@/lib/cache-preloader';

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

// Workspace card component
const WorkspaceCard = ({ workspace, onDelete, onEdit, router }) => (
  <Card key={workspace.name}>
    <CardHeader>
      <CardTitle className="text-base font-normal">
        <span className="font-semibold">Workspace:</span> {workspace.name}
      </CardTitle>
    </CardHeader>
    <CardContent className="text-sm pb-2">
      <div className="py-2 flex items-center justify-between">
        <div className="flex items-center text-gray-600">
          <ServerIcon className="w-4 h-4 mr-2 text-gray-500" />
          <span>Clusters (Running / Total)</span>
        </div>
        <button
          onClick={() => {
            router.push({
              pathname: '/clusters',
              query: { workspace: workspace.name },
            });
          }}
          className="font-normal text-blue-600 hover:text-blue-800 hover:underline cursor-pointer"
        >
          {workspace.runningClusterCount} / {workspace.totalClusterCount}
        </button>
      </div>
      <div className="py-2 flex items-center justify-between border-t border-gray-100">
        <div className="flex items-center text-gray-600">
          <BriefcaseIcon className="w-4 h-4 mr-2 text-gray-500" />
          <span>Managed Jobs</span>
        </div>
        <button
          onClick={() => {
            router.push({
              pathname: '/jobs',
              query: { workspace: workspace.name },
            });
          }}
          className="font-normal text-blue-600 hover:text-blue-800 hover:underline cursor-pointer"
        >
          {workspace.managedJobsCount}
        </button>
      </div>
    </CardContent>

    <div className="px-6 pb-3 text-sm pt-3">
      <h4 className="mb-2 text-xs text-gray-500 tracking-wider">
        Enabled Infra
      </h4>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {workspace.clouds.map((cloud) => (
          <div key={cloud} className="flex items-center text-gray-700">
            <TickIcon className="w-3.5 h-3.5 mr-1.5 text-green-500" />
            <span>{cloud}</span>
          </div>
        ))}
      </div>
    </div>

    <CardFooter className="flex justify-end pt-3 gap-2">
      <Button
        variant="outline"
        size="sm"
        onClick={() => onDelete(workspace.name)}
        disabled={workspace.name === 'default'}
        title={
          workspace.name === 'default'
            ? 'Cannot delete default workspace'
            : 'Delete workspace'
        }
        className="text-red-600 hover:text-red-700 hover:bg-red-50"
      >
        Delete
      </Button>
      <Button
        variant="outline"
        size="sm"
        onClick={() => onEdit(workspace.name)}
      >
        Edit
      </Button>
    </CardFooter>
  </Card>
);

// Create new workspace card component
const CreateWorkspaceCard = ({ onClick }) => (
  <Card
    key="create-new"
    className="border-2 border-dashed border-sky-300 hover:border-sky-400 cursor-pointer transition-colors flex flex-col"
    onClick={onClick}
  >
    <div className="flex-1 flex items-center justify-center p-6">
      <div className="text-center">
        <div className="w-16 h-16 rounded-full bg-sky-100 flex items-center justify-center mb-4 mx-auto">
          <span className="text-3xl text-sky-600">+</span>
        </div>
        <h3 className="text-lg font-medium text-sky-700 mb-2">
          Create New Workspace
        </h3>
        <p className="text-sm text-gray-500">
          Set up a new workspace with custom infrastructure configurations
        </p>
      </div>
    </div>
  </Card>
);

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
  const [loading, setLoading] = useState(true);
  const [rawWorkspacesData, setRawWorkspacesData] = useState(null);

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

  const router = useRouter();
  const isMobile = useMobile();

  const fetchData = async (showLoading = false) => {
    if (showLoading) {
      setLoading(true);
    }
    try {
      const [fetchedWorkspacesConfig, clustersResponse, managedJobsResponse] =
        await Promise.all([
          dashboardCache.get(getWorkspaces),
          dashboardCache.get(getClusters),
          dashboardCache.get(getManagedJobs),
        ]);

      setRawWorkspacesData(fetchedWorkspacesConfig);
      const configuredWorkspaceNames = Object.keys(fetchedWorkspacesConfig);

      // Fetch enabled clouds for all workspaces using cache
      const enabledCloudsArray = await Promise.all(
        configuredWorkspaceNames.map((wsName) =>
          dashboardCache.get(getEnabledClouds, [wsName])
        )
      );
      const enabledCloudsMap = Object.fromEntries(
        configuredWorkspaceNames.map((wsName, index) => [
          wsName,
          enabledCloudsArray[index],
        ])
      );

      // Build cluster to workspace mapping
      const clusterNameToWorkspace = Object.fromEntries(
        clustersResponse.map((c) => [c.cluster, c.workspace || 'default'])
      );

      // Initialize workspace stats
      const workspaceStatsAggregator = {};
      configuredWorkspaceNames.forEach((wsName) => {
        workspaceStatsAggregator[wsName] = {
          name: wsName,
          totalClusterCount: 0,
          runningClusterCount: 0,
          managedJobsCount: 0,
          clouds: new Set(),
        };
      });

      // Process clusters
      let totalRunningClusters = 0;
      clustersResponse.forEach((cluster) => {
        const wsName = cluster.workspace || 'default';
        if (!workspaceStatsAggregator[wsName]) {
          workspaceStatsAggregator[wsName] = {
            name: wsName,
            totalClusterCount: 0,
            runningClusterCount: 0,
            managedJobsCount: 0,
            clouds: new Set(),
          };
        }

        workspaceStatsAggregator[wsName].totalClusterCount++;
        if (cluster.status === 'RUNNING' || cluster.status === 'LAUNCHING') {
          workspaceStatsAggregator[wsName].runningClusterCount++;
          totalRunningClusters++;
        }
        if (cluster.cloud) {
          workspaceStatsAggregator[wsName].clouds.add(cluster.cloud);
        }
      });

      // Process managed jobs
      const jobs = managedJobsResponse.jobs || [];
      const activeJobStatuses = new Set(statusGroups.active);
      let activeGlobalManagedJobs = 0;

      jobs.forEach((job) => {
        const jobClusterName =
          job.cluster_name || (job.resources && job.resources.cluster_name);
        if (jobClusterName) {
          const wsName = clusterNameToWorkspace[jobClusterName];
          if (
            wsName &&
            workspaceStatsAggregator[wsName] &&
            activeJobStatuses.has(job.status)
          ) {
            workspaceStatsAggregator[wsName].managedJobsCount++;
          }
        }
        if (activeJobStatuses.has(job.status)) {
          activeGlobalManagedJobs++;
        }
      });

      // Finalize workspace details
      const finalWorkspaceDetails = Object.values(workspaceStatsAggregator)
        .filter((ws) => configuredWorkspaceNames.includes(ws.name))
        .map((ws) => ({
          ...ws,
          clouds: Array.isArray(enabledCloudsMap[ws.name])
            ? enabledCloudsMap[ws.name]
            : [],
        }))
        .sort((a, b) => a.name.localeCompare(b.name));

      setWorkspaceDetails(finalWorkspaceDetails);
      setGlobalStats({
        runningClusters: totalRunningClusters,
        totalClusters: clustersResponse.length,
        managedJobs: activeGlobalManagedJobs,
      });
    } catch (error) {
      console.error('Error fetching workspace data:', error);
      setWorkspaceDetails([]);
      setGlobalStats({ runningClusters: 0, totalClusters: 0, managedJobs: 0 });
    }
    if (showLoading) {
      setLoading(false);
    }
  };

  useEffect(() => {
    const initializeData = async () => {
      // Trigger cache preloading for workspaces page and background preload other pages
      await cachePreloader.preloadForPage('workspaces');

      fetchData(true); // Show loading on initial load
    };

    initializeData();

    // Set up refresh interval
    const interval = setInterval(() => {
      fetchData(false); // Don't show loading on background refresh
    }, REFRESH_INTERVAL);

    return () => clearInterval(interval);
  }, []);

  const handleDeleteWorkspace = (workspaceName) => {
    setDeleteState({
      confirmOpen: true,
      workspaceToDelete: workspaceName,
      deleting: false,
      error: null,
    });
  };

  const handleConfirmDelete = async () => {
    if (!deleteState.workspaceToDelete) return;

    setDeleteState((prev) => ({ ...prev, deleting: true, error: null }));
    try {
      await deleteWorkspace(deleteState.workspaceToDelete);
      setDeleteState({
        confirmOpen: false,
        workspaceToDelete: null,
        deleting: false,
        error: null,
      });
      await fetchData();
    } catch (error) {
      console.error('Error deleting workspace:', error);
      setDeleteState((prev) => ({
        ...prev,
        deleting: false,
        error: error,
      }));
    }
  };

  const handleRefresh = () => {
    // Invalidate cache to ensure fresh data is fetched
    dashboardCache.invalidate(getWorkspaces);
    dashboardCache.invalidate(getClusters);
    dashboardCache.invalidate(getManagedJobs);
    dashboardCache.invalidateFunction(getEnabledClouds); // This function has arguments

    fetchData(true); // Show loading on manual refresh
  };

  const handleCancelDelete = () => {
    setDeleteState({
      confirmOpen: false,
      workspaceToDelete: null,
      deleting: false,
      error: null,
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

  if (loading && workspaceDetails.length === 0) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress />
        <span className="ml-2 text-gray-500">Loading workspaces...</span>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base flex items-center">
          <span className="text-sky-blue leading-none">Workspaces</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => router.push('/config')}
            className="ml-4 px-2 py-1 text-xs"
            disabled={
              loading ||
              !rawWorkspacesData ||
              Object.keys(rawWorkspacesData).length === 0
            }
          >
            Edit All Configs
          </Button>
        </div>
        <div className="flex items-center">
          {loading && (
            <div className="flex items-center mr-2">
              <CircularProgress size={15} className="mt-0" />
              <span className="ml-2 text-gray-500 text-xs">Refreshing...</span>
            </div>
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

      {/* Statistics Summary */}
      <StatsSummary
        workspaceCount={workspaceDetails.length}
        runningClusters={globalStats.runningClusters}
        totalClusters={globalStats.totalClusters}
        managedJobs={globalStats.managedJobs}
        router={router}
      />

      {/* Workspace Cards */}
      {workspaceDetails.length === 0 && !loading ? (
        <div className="text-center py-10">
          <p className="text-lg text-gray-600">No workspaces found.</p>
          <p className="text-sm text-gray-500 mt-2">
            Create a cluster to see its workspace here.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {workspaceDetails.map((ws) => (
            <WorkspaceCard
              key={ws.name}
              workspace={ws}
              onDelete={handleDeleteWorkspace}
              onEdit={(name) => router.push(`/workspaces/${name}`)}
              router={router}
            />
          ))}
          <CreateWorkspaceCard onClick={() => router.push('/workspace/new')} />
        </div>
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

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteState.confirmOpen} onOpenChange={handleCancelDelete}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete Workspace</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete workspace &quot;
              {deleteState.workspaceToDelete}&quot;? This action cannot be
              undone.
            </DialogDescription>
          </DialogHeader>

          <ErrorDisplay
            error={deleteState.error}
            title="Deletion Failed"
            onDismiss={() =>
              setDeleteState((prev) => ({ ...prev, error: null }))
            }
          />

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
