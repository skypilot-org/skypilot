import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { JobDetails } from '@/components/jobs';
import { useClusterDetails } from '@/data/connectors/clusters';
import { CustomTooltip as Tooltip } from '@/components/utils';
import { RotateCwIcon } from 'lucide-react';
import { CircularProgress } from '@mui/material';
import { Status2Actions } from '@/components/jobs';

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
    <div className="flex items-center justify-between mb-6">
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
      if (refreshData) {
        await refreshData();
      } else {
        // Force a page refresh to reload the data
        router.replace(router.asPath);
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

export default JobDetailPage;
