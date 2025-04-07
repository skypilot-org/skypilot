import React, { useState } from 'react';
import { CircularProgress } from '@mui/material';
import { ClusterJobs } from '@/components/jobs';
import { useRouter } from 'next/router';
import { Layout } from '@/components/elements/layout';
import Link from 'next/link';
import {
  State2Actions,
} from '@/components/clusters';
import { Card } from '@/components/ui/card';
import {
  useClusterDetails,
} from '@/data/connectors/clusters';
import {
  RotateCwIcon,
} from 'lucide-react';
import { CustomTooltip as Tooltip } from '@/components/utils';
import {
  SSHInstructionsModal,
  VSCodeInstructionsModal,
} from '@/components/elements/modals';

function ClusterDetails() {
  const router = useRouter();
  const { cluster } = router.query; // Access the dynamic part of the URL

  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isSSHModalOpen, setIsSSHModalOpen] = useState(false);
  const [isVSCodeModalOpen, setIsVSCodeModalOpen] = useState(false);

  const { clusterData, clusterJobData, loading, refreshData } =
    useClusterDetails({ cluster });

  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    await refreshData();
    setIsRefreshing(false);
  };

  const handleConnectClick = () => {
    setIsSSHModalOpen(true);
  };

  const handleVSCodeClick = () => {
    setIsVSCodeModalOpen(true);
  };

  // Render loading state until data is available
  if (!router.isReady) {
    return <div>Loading...</div>;
  }

  return (
    <Layout highlighted="clusters">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-2xl flex items-center">
          <Link href="/clusters" className="text-sky-blue hover:underline">
            Sky Clusters
          </Link>
          <span className="mx-2 text-gray-500">›</span>
          <Link
            href={`/clusters/${cluster}`}
            className="text-sky-blue hover:underline"
          >
            {cluster}
          </Link>
        </h2>

        {clusterData && (
          <div className="flex items-center">
            <div className="flex items-center">
              {(loading || isRefreshing) && (
                <div className="flex items-center mr-2">
                  <CircularProgress size={15} className="mt-0" />
                  <span className="ml-2 text-gray-500">Loading...</span>
                </div>
              )}
              <Tooltip
                content="Refresh"
                className="text-sm text-muted-foreground"
              >
                <button
                  onClick={handleManualRefresh}
                  disabled={loading || isRefreshing}
                  className="text-sky-blue hover:text-sky-blue-bright font-medium mx-2 flex items-center"
                >
                  <RotateCwIcon className="w-4 h-4 mr-1.5" />
                  Refresh
                </button>
              </Tooltip>
            </div>
            <State2Actions
              withLabel={true}
              cluster={clusterData.cluster}
              state={clusterData.state}
              onOpenSSHModal={handleConnectClick}
              onOpenVSCodeModal={handleVSCodeClick}
            />
          </div>
        )}
      </div>

      <div className="border-b border-gray-200 my-4"></div>

      {clusterData && (
        <ActiveTab clusterData={clusterData} clusterJobData={clusterJobData} />
      )}

      {/* SSH Instructions Modal */}
      <SSHInstructionsModal
        isOpen={isSSHModalOpen}
        onClose={() => setIsSSHModalOpen(false)}
        cluster={cluster}
      />

      {/* VSCode Instructions Modal */}
      <VSCodeInstructionsModal
        isOpen={isVSCodeModalOpen}
        onClose={() => setIsVSCodeModalOpen(false)}
        cluster={cluster}
      />
    </Layout>
  );
}

function ActiveTab({ clusterData, clusterJobData }) {
  return (
    <div>
      {/* Cluster Info Card */}
      <h3 className="text-xl font-semibold mb-4">Details</h3>
      <div className="items-center mb-6">
        <Card className="p-3">
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div className="flex items-center">
              <span className="font-medium text-gray-600 mr-2">Cluster:</span>
              <span>{clusterData.cluster}</span>
            </div>
            <div className="flex items-center">
              <span className="font-medium text-gray-600 mr-2">User:</span>
              <span>{clusterData.user}</span>
            </div>
            <div className="flex items-center">
              <span className="font-medium text-gray-600 mr-2">Status:</span>
              <span>{clusterData.state}</span>
            </div>
            <div className="flex items-center">
              <span className="font-medium text-gray-600 mr-2">Infra:</span>
              <span>{clusterData.infra}</span>
            </div>
            <div className="flex items-center">
              <span className="font-medium text-gray-600 mr-2">Nodes:</span>
              <span>{clusterData.num_nodes}</span>
            </div>
            <div className="flex items-center">
              <span className="font-medium text-gray-600 mr-2">Resources:</span>
              <span>{clusterData.resources || 'N/A'}</span>
            </div>
          </div>
        </Card>
      </div>

      {/* Jobs Table */}
      <h3 className="text-lg font-semibold my-4">Cluster Jobs</h3>
      <div>
        {clusterJobData && (
          <ClusterJobs
            clusterName={clusterData.cluster}
            clusterJobData={clusterJobData}
          />
        )}
      </div>
    </div>
  );
}

export default ClusterDetails;
