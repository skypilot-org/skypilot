import React, { useState } from 'react';
import { CircularProgress } from '@mui/material';
import { ClusterJobs } from '@/components/jobs';
import { useRouter } from 'next/router';
import { Layout } from '@/components/elements/layout';
import Link from 'next/link';
import { Status2Actions } from '@/components/clusters';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { Card } from '@/components/ui/card';
import { useClusterDetails } from '@/data/connectors/clusters';
import { RotateCwIcon } from 'lucide-react';
import {
  CustomTooltip as Tooltip,
  NonCapitalizedTooltip,
} from '@/components/utils';
import {
  SSHInstructionsModal,
  VSCodeInstructionsModal,
} from '@/components/elements/modals';
import { useMobile } from '@/hooks/useMobile';
import Head from 'next/head';

function ClusterDetails() {
  const router = useRouter();
  const { cluster } = router.query; // Access the dynamic part of the URL

  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [isSSHModalOpen, setIsSSHModalOpen] = useState(false);
  const [isVSCodeModalOpen, setIsVSCodeModalOpen] = useState(false);
  const isMobile = useMobile();
  const { clusterData, clusterJobData, loading, refreshData } =
    useClusterDetails({ cluster });

  // Update isInitialLoad when data is first loaded
  React.useEffect(() => {
    if (!loading && isInitialLoad) {
      setIsInitialLoad(false);
    }
  }, [loading, isInitialLoad]);

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

  const title = cluster
    ? `Cluster: ${cluster} | SkyPilot Dashboard`
    : 'Cluster Details | SkyPilot Dashboard';

  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <Layout highlighted="clusters">
        <div className="flex items-center justify-between mb-4 h-5">
          <div className="text-base flex items-center">
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

          <div className="text-sm flex items-center">
            <div className="text-sm flex items-center">
              {(loading || isRefreshing) && (
                <div className="flex items-center mr-4">
                  <CircularProgress size={15} className="mt-0" />
                  <span className="ml-2 text-gray-500">Loading...</span>
                </div>
              )}
              {clusterData && (
                <div className="flex items-center space-x-4">
                  <Tooltip
                    content="Refresh"
                    className="text-sm text-muted-foreground"
                  >
                    <button
                      onClick={handleManualRefresh}
                      disabled={loading || isRefreshing}
                      className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center"
                    >
                      <RotateCwIcon className="w-4 h-4 mr-1.5" />
                      {!isMobile && <span>Refresh</span>}
                    </button>
                  </Tooltip>
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
          </div>
        </div>

        {loading && isInitialLoad ? (
          <div className="flex justify-center items-center py-12">
            <CircularProgress size={24} className="mr-2" />
            <span className="text-gray-500">Loading...</span>
          </div>
        ) : clusterData ? (
          <ActiveTab
            clusterData={clusterData}
            clusterJobData={clusterJobData}
            loading={loading || isRefreshing}
          />
        ) : null}

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
    </>
  );
}

function ActiveTab({ clusterData, clusterJobData, loading }) {
  return (
    <div>
      {/* Cluster Info Card */}
      <div className="mb-6">
        <Card>
          <div className="flex items-center justify-between px-4 pt-4">
            <h3 className="text-lg font-semibold">Details</h3>
          </div>
          <div className="p-4">
            <div className="grid grid-cols-2 gap-6">
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Status
                </div>
                <div className="text-base mt-1">
                  <StatusBadge status={clusterData.status} />
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Cluster
                </div>
                <div className="text-base mt-1">{clusterData.cluster}</div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">User</div>
                <div className="text-base mt-1">{clusterData.user}</div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">Infra</div>
                <div className="text-base mt-1">
                  {clusterData.infra ? (
                    <NonCapitalizedTooltip
                      content={clusterData.full_infra || clusterData.infra}
                      className="text-sm text-muted-foreground"
                    >
                      <span>
                        <Link
                          href="/infra"
                          className="text-blue-600 hover:underline"
                        >
                          {clusterData.cloud ||
                            clusterData.infra.split('(')[0].trim()}
                        </Link>
                        {clusterData.infra.includes('(') && (
                          <span>
                            {' ' +
                              clusterData.infra.substring(
                                clusterData.infra.indexOf('(')
                              )}
                          </span>
                        )}
                      </span>
                    </NonCapitalizedTooltip>
                  ) : (
                    'N/A'
                  )}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Resources
                </div>
                <div className="text-base mt-1">
                  {clusterData.resources_str_full ||
                    clusterData.resources_str ||
                    'N/A'}
                </div>
              </div>
              <div>
                <div className="text-gray-600 font-medium text-base">
                  Started
                </div>
                <div className="text-base mt-1">
                  {clusterData.time
                    ? new Date(clusterData.time).toLocaleString()
                    : 'N/A'}
                </div>
              </div>
            </div>
          </div>
        </Card>
      </div>

      {/* Jobs Table */}
      <div>
        {clusterJobData && (
          <ClusterJobs
            clusterName={clusterData.cluster}
            clusterJobData={clusterJobData}
            loading={loading}
          />
        )}
      </div>
    </div>
  );
}

export default ClusterDetails;
