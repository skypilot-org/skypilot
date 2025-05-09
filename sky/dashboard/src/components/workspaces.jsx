'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
import { getWorkspaces } from '@/data/connectors/workspaces';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardFooter,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { CircularProgress, Modal, Box, Typography, IconButton } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import {
  ServerIcon,
  BriefcaseIcon,
  CloudIcon,
  BookDocIcon,
  TickIcon,
} from '@/components/elements/icons';

export function Workspaces() {
  const [workspaceDetails, setWorkspaceDetails] = useState([]);
  const [globalRunningClusters, setGlobalRunningClusters] = useState(0);
  const [globalTotalClusters, setGlobalTotalClusters] = useState(0);
  const [globalManagedJobs, setGlobalManagedJobs] = useState(0);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  // State for the modal
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedWorkspaceConfig, setSelectedWorkspaceConfig] = useState(null);
  // State to store the raw fetched workspace configurations
  const [rawWorkspacesData, setRawWorkspacesData] = useState(null);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const [fetchedWorkspacesConfig, clustersResponse, managedJobsResponse] = await Promise.all([
          getWorkspaces(),
          getClusters(),
          getManagedJobs(),
        ]);

        console.log('[Workspaces Debug] Raw fetchedWorkspacesConfig:', fetchedWorkspacesConfig);
        setRawWorkspacesData(fetchedWorkspacesConfig); // Corrected: fetchedWorkspacesConfig

        const configuredWorkspaceNames = Object.keys(fetchedWorkspacesConfig);
        console.log('[Workspaces Debug] configuredWorkspaceNames:', configuredWorkspaceNames);

        const clusterNameToWorkspace = {};
        clustersResponse.forEach((c) => {
          clusterNameToWorkspace[c.cluster] = c.workspace || 'default';
        });

        let totalRunningClusters = 0;
        const workspaceStatsAggregator = {};

        if (configuredWorkspaceNames.length > 0) {
          configuredWorkspaceNames.forEach(wsName => {
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
          if (configuredWorkspaceNames.length > 0 && !workspaceStatsAggregator[wsName]) {
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
        
        let finalWorkspaceDetails = Object.values(workspaceStatsAggregator).map((ws) => ({
          ...ws,
          clouds: Array.from(ws.clouds).sort(),
        }));

        if (configuredWorkspaceNames.length > 0) {
            finalWorkspaceDetails = finalWorkspaceDetails.filter(ws => configuredWorkspaceNames.includes(ws.name));
        }

        finalWorkspaceDetails.sort((a, b) => a.name.localeCompare(b.name));

        console.log('[Workspaces Debug] finalWorkspaceDetails before setting state:', finalWorkspaceDetails);

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
    fetchData();
  }, []);

  const handleShowWorkspaceDetails = (workspaceName) => {
    if (rawWorkspacesData && rawWorkspacesData[workspaceName]) {
      setSelectedWorkspaceConfig(rawWorkspacesData[workspaceName]);
      setIsModalOpen(true);
    } else {
      console.error(`Configuration not found for workspace: ${workspaceName}`);
      // Optionally: show a toast notification if config is missing
    }
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setSelectedWorkspaceConfig(null);
  };

  // Style for the modal
  const modalStyle = {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    width: '80%',
    maxWidth: 600,
    bgcolor: 'background.paper',
    border: '2px solid #000',
    boxShadow: 24,
    p: 4,
    maxHeight: '90vh',
    overflowY: 'auto',
  };

  if (loading) {
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
                <CardTitle className="text-base font-medium">
                  Workspace: <span className="font-semibold">{ws.name}</span>
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

              <div className="px-6 pb-3 text-sm border-t border-gray-100 pt-3">
                <h4 className="mb-1 text-xs text-gray-500 uppercase tracking-wider">
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

              <CardFooter className="flex justify-end pt-3 border-t border-gray-100">
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
        <Modal
          open={isModalOpen}
          onClose={handleCloseModal}
          aria-labelledby="workspace-config-modal-title"
          aria-describedby="workspace-config-modal-description"
        >
          <Box sx={modalStyle}>
            <Box display="flex" justifyContent="space-between" alignItems="center">
              <Typography id="workspace-config-modal-title" variant="h6" component="h2">
                Workspace Configuration: {selectedWorkspaceConfig.name || Object.keys(rawWorkspacesData).find(key => rawWorkspacesData[key] === selectedWorkspaceConfig)}
              </Typography>
              <IconButton onClick={handleCloseModal} aria-label="close">
                <CloseIcon />
              </IconButton>
            </Box>
            <Typography id="workspace-config-modal-description" sx={{ mt: 2 }} component="div">
              <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {JSON.stringify(selectedWorkspaceConfig, null, 2)}
              </pre>
            </Typography>
          </Box>
        </Modal>
      )}
    </div>
  );
}
