import React, { useState, useEffect } from 'react';
import { Layout } from '@/components/elements/layout';
import { Card } from '@/components/ui/card';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { formatLogs } from '@/components/jobs';
import { useClusterDetails } from '@/data/connectors/clusters';
import { CustomTooltip as Tooltip } from '@/components/utils';
import { RotateCwIcon } from 'lucide-react';
import { CircularProgress } from '@mui/material';
import { streamClusterJobLogs } from '@/data/connectors/clusters';
import { Status2Icon, LogFilter } from '@/components/jobs';

// Custom header component with buttons inline
function JobHeader({
  cluster,
  job,
  jobData,
  onRefresh,
  isRefreshing,
  loading,
}) {
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
            <span>Refresh</span>
          </button>
        </Tooltip>
      </div>
    </div>
  );
}

export function JobDetailPage() {
  const router = useRouter();
  const { cluster, job } = router.query; // Access the dynamic part of the URL
  const { clusterData, clusterJobData, loading, refreshData } =
    useClusterDetails({ cluster });
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Handle manual refresh
  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    try {
      // Refresh the job data
      if (refreshData) {
        await refreshData();
      }

      // Wait a short time to show the refresh indicator
      await new Promise((resolve) => setTimeout(resolve, 1000));
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
    jobData = clusterJobData.find((j) => j.id == job);
    jobData.infra = clusterData.infra;
    jobData.cluster = clusterData.cluster;
    jobData.user = clusterData.user;
  }

  return (
    <JobDetails
      clusterName={cluster}
      jobData={jobData}
      parentLink={`/clusters/${cluster}`}
      withEvents={false}
      highlighted="clusters"
      loading={loading || isRefreshing}
      hideTabs={false}
      isRefreshing={isRefreshing}
      customHeader={
        <JobHeader
          cluster={cluster}
          job={job}
          jobData={jobData}
          onRefresh={handleManualRefresh}
          isRefreshing={isRefreshing}
          loading={loading}
        />
      }
    />
  );
}

function JobDetails({
  clusterName,
  jobData,
  parent,
  parentLink,
  withEvents = true,
  highlighted = 'jobs',
  actionButtons,
  customHeader,
  hideTabs = false,
  loading = false,
  isRefreshing = false,
}) {
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [isLoadingControllerLogs, setIsLoadingControllerLogs] = useState(false);
  const [logs, setLogs] = useState([]);
  const router = useRouter();
  const isClusterJobPage = router.pathname.includes(
    '/clusters/[cluster]/[job]'
  );
  const lastFetchedJobId = React.useRef(null);
  console.log(
    'cluster job details',
    'jobData.id',
    jobData.id,
    'isRefreshing',
    isRefreshing
  );

  // Single effect for log management
  useEffect(() => {
    console.log(
      'fetch logs',
      'clusterName',
      clusterName,
      'jobData.id',
      jobData.id,
      'isRefreshing',
      isRefreshing,
      'lastFetchedJobId',
      lastFetchedJobId.current
    );
    let active = true;

    // Only fetch if we haven't fetched this job's logs yet or if we're refreshing
    if (
      clusterName &&
      jobData.id &&
      (lastFetchedJobId.current !== jobData.id || isRefreshing)
    ) {
      setIsLoadingLogs(true);
      setLogs([]); // Clear logs before fetching new ones
      lastFetchedJobId.current = jobData.id;

      streamClusterJobLogs({
        clusterName: clusterName,
        jobId: jobData.id,
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
    }

    return () => {
      active = false;
    };
  }, [clusterName, jobData.id, isRefreshing]);

  return (
    <Layout highlighted={highlighted}>
      {customHeader || (
        <div className="flex items-center justify-between mb-4">
          <div className="text-base flex items-center">
            {parentLink && (
              <>
                <Link
                  href={parentLink}
                  className="text-sky-blue hover:underline"
                >
                  {parent}
                </Link>
                <span className="mx-2 text-gray-500">›</span>
              </>
            )}
            <span className="text-gray-900">{jobData.id}</span>
          </div>
        </div>
      )}

      {/* Display all sections directly on the page */}
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
                    User
                  </div>
                  <div className="text-base mt-1">{jobData.user}</div>
                </div>
                <div>
                  <div className="text-gray-600 font-medium text-base">
                    Status
                  </div>
                  <div className="text-base mt-1">
                    <Status2Icon status={jobData.status} />
                  </div>
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
              <h2 className="text-lg font-semibold">Logs</h2>
            </div>
            <div className="p-4">
              {isLoadingLogs ? (
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
    </Layout>
  );
}

export default JobDetailPage;
