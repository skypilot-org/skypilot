import React, { useState, useEffect, useMemo } from 'react';
import { Card } from '@/components/ui/card';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useClusterDetails } from '@/data/connectors/clusters';
import {
  CustomTooltip as Tooltip,
  formatFullTimestamp,
  LogFilter,
} from '@/components/utils';
import { RotateCwIcon, Download } from 'lucide-react';
import { CircularProgress } from '@mui/material';
import {
  streamClusterJobLogs,
  downloadJobLogs,
} from '@/data/connectors/clusters';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { useMobile } from '@/hooks/useMobile';
import Head from 'next/head';
import { UserDisplay } from '@/components/elements/UserDisplay';
import { CheckIcon, CopyIcon } from 'lucide-react';
import PropTypes from 'prop-types';
import { useLogStreamer } from '@/hooks/useLogStreamer';
import { useCallback } from 'react';

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
  const [isCopied, setIsCopied] = useState(false);
  const [logsRefreshToken, setLogsRefreshToken] = useState(0);

  const PENDING_STATUSES = useMemo(() => ['INIT', 'PENDING', 'SETTING_UP'], []);

  const isPending = useMemo(() => {
    if (!clusterJobData || !job) return true;
    const jobData = clusterJobData.find((j) => j.id == job);
    return jobData && PENDING_STATUSES.includes(jobData.status);
  }, [clusterJobData, job, PENDING_STATUSES]);

  // Update isInitialLoad when data is first loaded
  useEffect(() => {
    if (!loading && isInitialLoad) {
      setIsInitialLoad(false);
    }
  }, [loading, isInitialLoad]);

  const logStreamArgs = useMemo(
    () => ({
      clusterName: cluster,
      jobId: job,
      workspace: clusterData?.workspace,
    }),
    [cluster, job, clusterData?.workspace]
  );

  const handleStreamError = useCallback((error) => {
    console.error('Error streaming cluster logs:', error);
  }, []);

  const { lines: displayLines, isLoading: isLoadingLogs } = useLogStreamer({
    streamFn: streamClusterJobLogs,
    streamArgs: logStreamArgs,
    enabled: Boolean(cluster && job) && !isPending,
    refreshTrigger: logsRefreshToken,
    onError: handleStreamError,
  });

  const handleRefreshLogs = () => {
    setLogsRefreshToken((token) => token + 1);
  };

  // Handle manual refresh
  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    setLogsRefreshToken((token) => token + 1);
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
        user_hash: clusterData.user_hash,
      };
    }
  }

  const title =
    cluster && job
      ? `Job: ${job} @ ${cluster} | SkyPilot Dashboard`
      : 'Job Details | SkyPilot Dashboard';

  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <>
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
                      <div className="text-base mt-1">
                        <UserDisplay
                          username={jobData.user}
                          userHash={jobData.user_hash}
                        />
                      </div>
                    </div>
                    <div>
                      <div className="text-gray-600 font-medium text-base">
                        Submitted
                      </div>
                      <div className="text-base mt-1">
                        {jobData.submitted_at
                          ? formatFullTimestamp(jobData.submitted_at)
                          : 'N/A'}
                      </div>
                    </div>
                    {jobData.resources && (
                      <div>
                        <div className="text-gray-600 font-medium text-base">
                          Requested Resources
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
                    <div>
                      <div className="text-gray-600 font-medium text-base">
                        Git Commit
                      </div>
                      <div className="text-base mt-1 flex items-center">
                        {jobData.git_commit && jobData.git_commit !== '-' ? (
                          <span className="flex items-center mr-2">
                            {jobData.git_commit}
                            <Tooltip
                              content={isCopied ? 'Copied!' : 'Copy commit'}
                              className="text-muted-foreground"
                            >
                              <button
                                onClick={async () => {
                                  await navigator.clipboard.writeText(
                                    jobData.git_commit
                                  );
                                  setIsCopied(true);
                                  setTimeout(() => setIsCopied(false), 2000);
                                }}
                                className="flex items-center text-gray-500 hover:text-gray-700 transition-colors duration-200 p-1 ml-2"
                              >
                                {isCopied ? (
                                  <CheckIcon className="w-4 h-4 text-green-600" />
                                ) : (
                                  <CopyIcon className="w-4 h-4" />
                                )}
                              </button>
                            </Tooltip>
                          </span>
                        ) : (
                          <span className="text-gray-400">-</span>
                        )}
                      </div>
                    </div>
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
                  <div className="flex items-center space-x-3">
                    <Tooltip
                      content="Download full logs"
                      className="text-muted-foreground"
                    >
                      <button
                        onClick={() =>
                          downloadJobLogs({
                            clusterName: cluster,
                            jobIds: job ? [job] : null,
                            workspace: clusterData?.workspace,
                          })
                        }
                        className="text-sky-blue hover:text-sky-blue-bright flex items-center"
                      >
                        <Download className="w-4 h-4" />
                      </button>
                    </Tooltip>
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
                </div>
                <div className="p-4">
                  {isPending ? (
                    <div className="bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500">
                      <span>
                        Waiting for the job to start; refresh in a few moments.
                      </span>
                    </div>
                  ) : isLoadingLogs ? (
                    <div className="flex items-center justify-center py-4">
                      <CircularProgress size={20} className="mr-2" />
                      <span>Loading...</span>
                    </div>
                  ) : (
                    <div className="max-h-96 overflow-y-auto">
                      <LogFilter logs={displayLines} />
                    </div>
                  )}
                </div>
              </Card>
            </div>
          </div>
        )}
      </>
    </>
  );
}

JobHeader.propTypes = {
  cluster: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  job: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  jobData: PropTypes.shape({
    job: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  }),
  onRefresh: PropTypes.func,
  isRefreshing: PropTypes.bool,
  loading: PropTypes.bool,
};

export default JobDetailPage;
