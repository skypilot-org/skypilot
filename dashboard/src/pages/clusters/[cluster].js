import React, { useState } from 'react';
import { CircularProgress } from '@mui/material';
import { ClusterJobs } from '@/components/jobs';
import { useRouter } from 'next/router';
import { Layout } from '@/components/elements/layout';
import Link from 'next/link';
import {
  Status2Actions,
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
        <div className="text-sm flex items-center">
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
        </div>

        {clusterData && (
          <div className="text-sm flex items-center">
            <div className="text-sm flex items-center">
              {(loading || isRefreshing) && (
                <div className="flex items-center mr-2">
                  <CircularProgress size={10} className="mt-0" />
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
            <Status2Actions
              withLabel={true}
              cluster={clusterData.cluster}
              status={clusterData.status}
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
          <div className="grid grid-cols-2 gap-6">
            <div>
              <div className="text-gray-600 font-medium text-lg">Cluster</div>
              <div className="text-sm mt-1">{clusterData.cluster}</div>
            </div>
            <div>
              <div className="text-gray-600 font-medium text-lg">User</div>
              <div className="text-sm mt-1">{clusterData.user}</div>
            </div>
            <div>
              <div className="text-gray-600 font-medium text-lg">Status</div>
              <div className="text-sm mt-1">{clusterData.status}</div>
            </div>
            <div>
              <div className="text-gray-600 font-medium text-lg">Resources</div>
              <div className="text-sm mt-1">{clusterData.resources_str || 'N/A'}</div>
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
