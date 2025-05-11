import React, { useState, useEffect, useMemo } from 'react';
import { Layout } from '@/components/elements/layout';
import { Card } from '@/components/ui/card';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useClusterDetails } from '@/data/connectors/clusters';
import { CustomTooltip as Tooltip } from '@/components/utils';
import { RotateCwIcon } from 'lucide-react';
import { CircularProgress } from '@mui/material';
import { streamClusterJobLogs } from '@/data/connectors/clusters';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { LogFilter, formatLogs } from '@/components/utils';
import { useMobile } from '@/hooks/useMobile';

// Custom header component with buttons inline
function JobHeader({
  cluster,
  job,
  jobData,
  onRefresh,
  isRefreshing,
  loading,
}) {
  const isMobile = useMobile();
  return (
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
        <span className="mx-2 text-gray-500">›</span>
        <Link
          href={`/clusters/${cluster}/${job}`}
          className="text-sky-blue hover:underline"
        >
          {job}
          {jobData.job && jobData.job != '-' ? ` (${jobData.job})` : ''}
        </Link>
      </div>

      <div className="flex items-center">
        {(loading || isRefreshing) && (
          <div className="flex items-center mr-2">
            <CircularProgress size={15} className="mt-0" />
            <span className="text-sm ml-2 text-gray-500">Loading...</span>
          </div>
        )}
        <Tooltip content="Refresh" className="text-muted-foreground">
          <button
            onClick={onRefresh}
            disabled={loading || isRefreshing}
            className="text-sm text-sky-blue hover:text-sky-blue-bright font-medium mx-2 flex items-center"
          >
            <RotateCwIcon className="w-4 h-4 mr-1.5" />
            {!isMobile && <span>Refresh</span>}
          </button>
        </Tooltip>
      </div>
    </div>
  );
}

export function JobDetailPage() {
  const router = useRouter();
  const { cluster, job } = router.query;
  const { clusterData, clusterJobData, loading, refreshData } =
    useClusterDetails({ cluster });
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [logs, setLogs] = useState([]);
  const [isRefreshingLogs, setIsRefreshingLogs] = useState(false);

  const PENDING_STATUSES = useMemo(() => ['INIT', 'PENDING', 'SETTING_UP'], []);

  const isPending = useMemo(() => {
    if (!clusterJobData || !job) return true;
    const jobData = clusterJobData.find((j) => j.id == job);
    return jobData && PENDING_STATUSES.includes(jobData.status);
  }, [clusterJobData, job, PENDING_STATUSES]);

  // Update isInitialLoad when data is first loaded
  React.useEffect(() => {
    if (!loading && isInitialLoad) {
      setIsInitialLoad(false);
    }
  }, [loading, isInitialLoad]);

  const handleRefreshLogs = () => {
    setIsRefreshingLogs((prev) => !prev);
    setLogs([]);
  };

  useEffect(() => {
    let active = true;

    if (!cluster || !job || isPending) {
      setIsLoadingLogs(false);
      return () => {
        active = false;
      };
    }

    setIsLoadingLogs(true);

    streamClusterJobLogs({
      clusterName: cluster,
      jobId: job,
      onNewLog: (log) => {
        if (active) {
          const strippedLog = formatLogs(log);
          setLogs((prevLogs) => [...prevLogs, strippedLog]);
        }
      },
    })
      .then(() => {
        if (active) {
          setIsLoadingLogs(false);
        }
      })
      .catch((error) => {
        if (active) {
          console.error('Error streaming logs:', error);
          setIsLoadingLogs(false);
        }
      });

    return () => {
      active = false;
    };
  }, [cluster, job, isRefreshingLogs, isPending]);

  // Handle manual refresh
  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    setIsRefreshingLogs((prev) => !prev);
    setLogs([]);
    try {
      if (refreshData) {
        await refreshData();
      }
    } catch (error) {
      console.error('Error refreshing data:', error);
    } finally {
      setIsRefreshing(false);
    }
  };

  // Render loading state until data is available
  if (!router.isReady) {
    return <div>Loading...</div>;
  }

  let jobData = {
    id: job,
  };

  if (clusterData && clusterJobData) {
    const foundJob = clusterJobData.find((j) => j.id == job);
    if (foundJob) {
      jobData = {
        ...foundJob,
        infra: clusterData.infra,
        cluster: clusterData.cluster,
        user: clusterData.user,
      };
    }
  }

  return (
    <Layout highlighted="clusters">
      <JobHeader
        cluster={cluster}
        job={job}
        jobData={jobData}
        onRefresh={handleManualRefresh}
        isRefreshing={isRefreshing}
        loading={loading}
      />
      {loading && isInitialLoad ? (
        <div className="flex items-center justify-center h-64">
          <CircularProgress size={24} className="mr-2" />
          <span>Loading...</span>
        </div>
      ) : (
        <div className="space-y-8">
          {/* Info Section */}
          <div id="details">
            <Card>
              <div className="flex items-center justify-between px-4 pt-4">
                <h2 className="text-lg font-semibold">Details</h2>
              </div>
              <div className="p-4">
                <div className="grid grid-cols-2 gap-6">
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      Job ID
                    </div>
                    <div className="text-base mt-1">{jobData.id}</div>
                  </div>
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      Job Name
                    </div>
                    <div className="text-base mt-1">{jobData.job}</div>
                  </div>
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      Status
                    </div>
                    <div className="text-base mt-1">
                      <StatusBadge status={jobData.status} />
                    </div>
                  </div>
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      User
                    </div>
                    <div className="text-base mt-1">{jobData.user}</div>
                  </div>
                  {jobData.resources && (
                    <div>
                      <div className="text-gray-600 font-medium text-base">
                        Resources
                      </div>
                      <div className="text-base mt-1">
                        {jobData.resources || 'N/A'}
                      </div>
                    </div>
                  )}
                  {jobData.cluster && (
                    <div>
                      <div className="text-gray-600 font-medium text-base">
                        Cluster
                      </div>
                      <div className="text-base mt-1">
                        <Link
                          href={`/clusters/${jobData.cluster}`}
                          className="text-sky-blue hover:underline"
                        >
                          {jobData.cluster}
                        </Link>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </Card>
          </div>

          {/* Logs Section */}
          <div id="logs" className="mt-6">
            <Card>
              <div className="flex items-center justify-between px-4 pt-4">
                <div className="flex items-center">
                  <h2 className="text-lg font-semibold">Logs</h2>
                  <span className="ml-2 text-xs text-gray-500">
                    (Logs are not streaming; click refresh to fetch the latest
                    logs.)
                  </span>
                </div>
                <Tooltip
                  content="Refresh logs"
                  className="text-muted-foreground"
                >
                  <button
                    onClick={handleRefreshLogs}
                    disabled={isLoadingLogs}
                    className="text-sky-blue hover:text-sky-blue-bright flex items-center"
                  >
                    <RotateCwIcon
                      className={`w-4 h-4 ${isLoadingLogs ? 'animate-spin' : ''}`}
                    />
                  </button>
                </Tooltip>
              </div>
              <div className="p-4">
                {isPending ? (
                  <div className="bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500">
                    <span>
                      Waiting for the job to start, please refresh after a while
                    </span>
                  </div>
                ) : isLoadingLogs ? (
                  <div className="flex items-center justify-center py-4">
                    <CircularProgress size={20} className="mr-2" />
                    <span>Loading...</span>
                  </div>
                ) : (
                  <div className="max-h-96 overflow-y-auto">
                    <LogFilter logs={logs.join('')} />
                  </div>
                )}
              </div>
            </Card>
          </div>
        </div>
      )}
    </Layout>
  );
}

export default JobDetailPage;
