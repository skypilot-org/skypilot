'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
import { getWorkspaces, getEnabledClouds } from '@/data/connectors/workspaces';
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
  DialogClose,
} from '@/components/ui/dialog';
import {
  ServerIcon,
  BriefcaseIcon,
  CloudIcon,
  BookDocIcon,
  TickIcon,
} from '@/components/elements/icons';
import { RotateCwIcon } from 'lucide-react';
import { useMobile } from '@/hooks/useMobile';

export function Workspaces() {
  const [workspaceDetails, setWorkspaceDetails] = useState([]);
  const [globalRunningClusters, setGlobalRunningClusters] = useState(0);
  const [globalTotalClusters, setGlobalTotalClusters] = useState(0);
  const [globalManagedJobs, setGlobalManagedJobs] = useState(0);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedWorkspaceConfig, setSelectedWorkspaceConfig] = useState(null);
  const [modalDisplayTitleName, setModalDisplayTitleName] = useState('');
  const [rawWorkspacesData, setRawWorkspacesData] = useState(null);

  const [isAllWorkspacesModalOpen, setIsAllWorkspacesModalOpen] =
    useState(false);

  const isMobile = useMobile();

  const fetchData = async () => {
    setLoading(true);
    try {
      const [fetchedWorkspacesConfig, clustersResponse, managedJobsResponse] =
        await Promise.all([getWorkspaces(), getClusters(), getManagedJobs()]);

      console.log(
        '[Workspaces Debug] Raw fetchedWorkspacesConfig:',
        fetchedWorkspacesConfig
      );
      setRawWorkspacesData(fetchedWorkspacesConfig);

      const configuredWorkspaceNames = Object.keys(fetchedWorkspacesConfig);
      console.log(
        '[Workspaces Debug] configuredWorkspaceNames:',
        configuredWorkspaceNames
      );

      const enabledCloudsPromises = configuredWorkspaceNames.map((wsName) =>
        getEnabledClouds(wsName)
      );
      const enabledCloudsForAllWorkspacesArray = await Promise.all(
        enabledCloudsPromises
      );

      const enabledCloudsMap = {};
      configuredWorkspaceNames.forEach((wsName, index) => {
        enabledCloudsMap[wsName] = enabledCloudsForAllWorkspacesArray[index];
      });

      console.log(
        '[Workspaces Debug] Enabled clouds by workspace:',
        enabledCloudsMap
      );

      const clusterNameToWorkspace = {};
      clustersResponse.forEach((c) => {
        clusterNameToWorkspace[c.cluster] = c.workspace || 'default';
      });

      let totalRunningClusters = 0;
      const workspaceStatsAggregator = {};

      if (configuredWorkspaceNames.length > 0) {
        configuredWorkspaceNames.forEach((wsName) => {
          workspaceStatsAggregator[wsName] = {
            name: wsName,
            totalClusterCount: 0,
            runningClusterCount: 0,
            managedJobsCount: 0,
            clouds: new Set(),
          };
        });
      }

      clustersResponse.forEach((cluster) => {
        const wsName = cluster.workspace || 'default';
        if (
          configuredWorkspaceNames.length > 0 &&
          !workspaceStatsAggregator[wsName]
        ) {
          if (!workspaceStatsAggregator[wsName]) {
            workspaceStatsAggregator[wsName] = {
              name: wsName,
              totalClusterCount: 0,
              runningClusterCount: 0,
              managedJobsCount: 0,
              clouds: new Set(),
            };
          }
        } else if (!workspaceStatsAggregator[wsName]) {
          workspaceStatsAggregator[wsName] = {
            name: wsName,
            totalClusterCount: 0,
            runningClusterCount: 0,
            managedJobsCount: 0,
            clouds: new Set(),
          };
        }

        workspaceStatsAggregator[wsName].totalClusterCount++;
        if (cluster.status === 'RUNNING') {
          workspaceStatsAggregator[wsName].runningClusterCount++;
          totalRunningClusters++;
        }
        if (cluster.cloud) {
          workspaceStatsAggregator[wsName].clouds.add(cluster.cloud);
        }
      });

      setGlobalTotalClusters(clustersResponse.length);
      setGlobalRunningClusters(totalRunningClusters);

      const jobs = managedJobsResponse.jobs || [];
      jobs.forEach((job) => {
        const jobClusterName =
          job.cluster_name || (job.resources && job.resources.cluster_name);
        if (jobClusterName) {
          const wsName = clusterNameToWorkspace[jobClusterName];
          if (wsName && workspaceStatsAggregator[wsName]) {
            workspaceStatsAggregator[wsName].managedJobsCount++;
          }
        }
      });

      setGlobalManagedJobs(jobs.length);

      let finalWorkspaceDetails = Object.values(workspaceStatsAggregator).map(
        (ws) => {
          const workspaceSpecificEnabledClouds =
            enabledCloudsMap[ws.name] || [];
          return {
            ...ws,
            clouds: Array.isArray(workspaceSpecificEnabledClouds)
              ? workspaceSpecificEnabledClouds.sort()
              : [],
          };
        }
      );

      if (configuredWorkspaceNames.length > 0) {
        finalWorkspaceDetails = finalWorkspaceDetails.filter((ws) =>
          configuredWorkspaceNames.includes(ws.name)
        );
      }

      finalWorkspaceDetails.sort((a, b) => a.name.localeCompare(b.name));

      console.log(
        '[Workspaces Debug] finalWorkspaceDetails before setting state:',
        finalWorkspaceDetails
      );

      setWorkspaceDetails(finalWorkspaceDetails);
    } catch (error) {
      console.error('Error fetching comprehensive workspace data:', error);
      setWorkspaceDetails([]);
      setGlobalRunningClusters(0);
      setGlobalTotalClusters(0);
      setGlobalManagedJobs(0);
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleShowWorkspaceDetails = (workspaceName) => {
    if (rawWorkspacesData && rawWorkspacesData[workspaceName]) {
      setSelectedWorkspaceConfig({
        [workspaceName]: rawWorkspacesData[workspaceName],
      });
      setModalDisplayTitleName(workspaceName);
      setIsModalOpen(true);
    } else {
      console.error(`Configuration not found for workspace: ${workspaceName}`);
    }
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setSelectedWorkspaceConfig(null);
    setModalDisplayTitleName('');
  };

  const handleShowAllWorkspacesConfig = () => {
    if (rawWorkspacesData && Object.keys(rawWorkspacesData).length > 0) {
      setIsAllWorkspacesModalOpen(true);
    } else {
      console.warn('Raw workspaces data is not available or empty.');
    }
  };

  const handleCloseAllWorkspacesModal = () => {
    setIsAllWorkspacesModalOpen(false);
  };

  const handleRefresh = () => {
    fetchData();
  };

  const preStyle = {
    backgroundColor: '#f5f5f5',
    padding: '16px',
    borderRadius: '8px',
    overflowX: 'auto',
    whiteSpace: 'pre',
    wordBreak: 'normal',
  };

  // Define the description component here
  const WorkspaceConfigDescription = ({ workspaceName, config }) => {
    // config is an object like { gcp: { disabled: true } } or { gcp: { project_id: '...' } } or {}
    if (!config) return null;

    const isDefault = workspaceName === 'default';
    const isEmptyConfig = Object.keys(config).length === 0;

    if (isDefault && isEmptyConfig) {
      return (
        <p className="text-sm text-gray-500 mb-3 italic p-3 bg-sky-50 rounded border border-sky-200">
          Workspace &apos;default&apos; can use all accessible infrastructure.
        </p>
      );
    }

    const descriptions = [];
    for (const cloud in config) {
      const cloudConfig = config[cloud];
      const cloudNameUpper = cloud.toUpperCase();

      if (cloudConfig && cloudConfig.disabled === true) {
        descriptions.push(
          <span key={`${cloud}-disabled`} className="block">
            {cloudNameUpper} is explicitly disabled.
          </span>
        );
      } else if (cloudConfig && Object.keys(cloudConfig).length > 0) {
        let detail = '';
        if (cloud.toLowerCase() === 'gcp' && cloudConfig.project_id) {
          detail = ` (Project ID: ${cloudConfig.project_id})`;
        } else if (cloud.toLowerCase() === 'aws' && cloudConfig.region) {
          // Example, can be expanded
          detail = ` (Region: ${cloudConfig.region})`;
        }
        descriptions.push(
          <span key={`${cloud}-enabled`} className="block">
            {cloudNameUpper}
            {detail} is enabled.
          </span>
        );
      } else if (cloudConfig && Object.keys(cloudConfig).length === 0) {
        descriptions.push(
          <span key={`${cloud}-default-enabled`} className="block">
            {cloudNameUpper} is enabled (using default settings).
          </span>
        );
      }
    }

    if (descriptions.length > 0) {
      return (
        <div className="text-sm text-gray-700 mb-3 p-3 bg-sky-50 rounded border border-sky-200">
          {descriptions}
          <p className="mt-2 text-gray-500">
            Other accessible infrastructure are enabled. See{' '}
            <code className="text-sky-blue">Enabled Infra</code>.
          </p>
        </div>
      );
    }

    if (!isDefault && isEmptyConfig) {
      return (
        <p className="text-sm text-gray-500 mb-3 italic p-3 bg-sky-50 rounded border border-sky-200">
          This workspace has no specific cloud resource configurations and can
          use all accessible infrastructure.
        </p>
      );
    }
    return null;
  };

  if (loading && workspaceDetails.length === 0) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress />
        <span className="ml-2 text-gray-500">Loading workspace data...</span>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base flex items-center">
          <span className="text-sky-blue leading-none">Workspaces</span>
          <Button
            variant="outline"
            size="sm"
            onClick={handleShowAllWorkspacesConfig}
            className="ml-4 px-2 py-1 text-xs"
            disabled={
              loading ||
              !rawWorkspacesData ||
              Object.keys(rawWorkspacesData).length === 0
            }
          >
            View All Configs
          </Button>
        </div>
        <div className="flex items-center">
          {loading && (
            <div className="flex items-center mr-2">
              <CircularProgress size={15} className="mt-0" />
              <span className="ml-2 text-gray-500 text-xs">Refreshing...</span>
            </div>
          )}
          <Button
            variant="ghost"
            onClick={handleRefresh}
            disabled={loading}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            {!isMobile && <span>Refresh</span>}
          </Button>
        </div>
      </div>

      <div className="bg-sky-50 p-4 rounded-lg shadow mb-6">
        <div className="flex flex-col sm:flex-row justify-around items-center">
          <div className="p-2">
            <div className="flex items-center">
              <BookDocIcon className="w-5 h-5 mr-2 text-sky-600" />
              <span className="text-sm text-gray-600">Workspaces:</span>
              <span className="ml-1 text-xl font-semibold text-sky-700">
                {workspaceDetails.length}
              </span>
            </div>
          </div>
          <div className="p-2">
            <div className="flex items-center">
              <ServerIcon className="w-5 h-5 mr-2 text-sky-600" />
              <span className="text-sm text-gray-600">
                Clusters (Running / Total):
              </span>
              <span className="ml-1 text-xl font-semibold text-sky-700">
                {globalRunningClusters} / {globalTotalClusters}
              </span>
            </div>
          </div>
          <div className="p-2">
            <div className="flex items-center">
              <BriefcaseIcon className="w-5 h-5 mr-2 text-sky-600" />
              <span className="text-sm text-gray-600">Managed Jobs:</span>
              <span className="ml-1 text-xl font-semibold text-sky-700">
                {globalManagedJobs}
              </span>
            </div>
          </div>
        </div>
      </div>

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
            <Card key={ws.name}>
              <CardHeader>
                <CardTitle className="text-base font-normal">
                  <span className="font-semibold">Workspace:</span> {ws.name}
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm pb-2">
                <div className="py-2 flex items-center justify-between">
                  <div className="flex items-center text-gray-600">
                    <ServerIcon className="w-4 h-4 mr-2 text-gray-500" />
                    <span>Clusters (Running / Total)</span>
                  </div>
                  <span className="font-normal text-gray-800">
                    {ws.runningClusterCount} / {ws.totalClusterCount}
                  </span>
                </div>
                <div className="py-2 flex items-center justify-between border-t border-gray-100">
                  <div className="flex items-center text-gray-600">
                    <BriefcaseIcon className="w-4 h-4 mr-2 text-gray-500" />
                    <span>Managed Jobs</span>
                  </div>
                  <span className="font-normal text-gray-800">
                    {ws.managedJobsCount}
                  </span>
                </div>
              </CardContent>

              <div className="px-6 pb-3 text-sm pt-3">
                <h4 className="mb-2 text-xs text-gray-500 tracking-wider">
                  Enabled Infra
                </h4>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {ws.clouds.map((cloud) => (
                    <div
                      key={cloud}
                      className="flex items-center text-gray-700"
                    >
                      <TickIcon className="w-3.5 h-3.5 mr-1.5 text-green-500" />
                      <span>{cloud}</span>
                    </div>
                  ))}
                </div>
              </div>

              <CardFooter className="flex justify-end pt-3">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleShowWorkspaceDetails(ws.name)}
                >
                  Details
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}

      {selectedWorkspaceConfig && (
        <Dialog open={isModalOpen} onOpenChange={handleCloseModal}>
          <DialogContent className="sm:max-w-md md:max-w-lg lg:max-w-xl xl:max-w-2xl w-full max-h-[90vh] flex flex-col">
            <DialogHeader>
              <DialogTitle className="pr-10">
                Workspace:
                <span className="font-normal"> {modalDisplayTitleName}</span>
              </DialogTitle>
            </DialogHeader>
            <div className="flex-grow overflow-y-auto py-4">
              {selectedWorkspaceConfig &&
                modalDisplayTitleName &&
                rawWorkspacesData &&
                rawWorkspacesData[modalDisplayTitleName] && (
                  <WorkspaceConfigDescription
                    workspaceName={modalDisplayTitleName}
                    config={rawWorkspacesData[modalDisplayTitleName]}
                  />
                )}
              <pre style={preStyle}>
                {yaml.dump(selectedWorkspaceConfig, { indent: 2 })}
              </pre>
            </div>
          </DialogContent>
        </Dialog>
      )}

      {rawWorkspacesData && (
        <Dialog
          open={isAllWorkspacesModalOpen}
          onOpenChange={handleCloseAllWorkspacesModal}
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
    </div>
  );
}
