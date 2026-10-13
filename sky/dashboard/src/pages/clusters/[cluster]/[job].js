import React, { useState, useEffect, useMemo } from 'react';
import { Card } from '@/components/ui/card';
import Link from 'next/link';
import { useRouter } from 'next/router';
import {
  useClusterDetails,
  getClusterJobSpec,
} from '@/data/connectors/clusters';
import {
  CustomTooltip as Tooltip,
  formatFullTimestamp,
  LogFilter,
} from '@/components/utils';
import {
  RotateCwIcon,
  Download,
  ChevronDownIcon,
  ChevronRightIcon,
} from 'lucide-react';
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
import { formatYaml } from '@/lib/yamlUtils';
import { YamlCodeBlock } from '@/components/ui/yaml-code-block';
import {
  LINK_SCOPE_JOBS,
  normalizeUrl,
  useLogLinkExtractor,
  useScopedLinks,
  useTemplateLinks,
} from '@/utils/externalLinks';

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

// The entrypoint command and task YAML the job was submitted with. Renders
// nothing for jobs without a recorded spec (submitted before the API server
// recorded them).
function JobSpecSection({ spec }) {
  const [isYamlExpanded, setIsYamlExpanded] = useState(false);
  const [isYamlCopied, setIsYamlCopied] = useState(false);
  const [isCommandCopied, setIsCommandCopied] = useState(false);

  const entrypoint = spec?.entrypoint;
  const taskYaml = spec?.task_yaml;
  const hasYaml = Boolean(taskYaml) && taskYaml.trim() !== '{}';
  if (!entrypoint && !hasYaml) {
    return null;
  }

  const copy = async (text, setCopied) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy to clipboard:', err);
    }
  };

  return (
    <div className="col-span-2">
      <div className="flex items-center">
        <div className="text-gray-600 font-medium text-base">Entrypoint</div>
        {entrypoint && (
          <Tooltip
            content={isCommandCopied ? 'Copied!' : 'Copy command'}
            className="text-muted-foreground"
          >
            <button
              title="Copy command"
              onClick={() => copy(entrypoint, setIsCommandCopied)}
              className="flex items-center text-gray-500 hover:text-gray-700 transition-colors duration-200 p-1 ml-2"
            >
              {isCommandCopied ? (
                <CheckIcon className="w-4 h-4 text-green-600" />
              ) : (
                <CopyIcon className="w-4 h-4" />
              )}
            </button>
          </Tooltip>
        )}
      </div>

      <div className="space-y-4 mt-3">
        {entrypoint && (
          <div className="bg-gray-50 border border-gray-200 rounded-md p-3">
            <code className="text-sm text-gray-800 font-mono break-all">
              {entrypoint}
            </code>
          </div>
        )}

        {hasYaml && (
          <div>
            <div className="flex items-center mb-2">
              <button
                onClick={() => setIsYamlExpanded(!isYamlExpanded)}
                className="flex items-center text-left focus:outline-none text-gray-700 hover:text-gray-900 transition-colors duration-200"
              >
                {isYamlExpanded ? (
                  <ChevronDownIcon className="w-4 h-4 mr-1" />
                ) : (
                  <ChevronRightIcon className="w-4 h-4 mr-1" />
                )}
                <span className="text-base">Show SkyPilot YAML</span>
              </button>

              <Tooltip
                content={isYamlCopied ? 'Copied!' : 'Copy YAML'}
                className="text-muted-foreground"
              >
                <button
                  title="Copy YAML"
                  onClick={() => copy(formatYaml(taskYaml), setIsYamlCopied)}
                  className="flex items-center text-gray-500 hover:text-gray-700 transition-colors duration-200 p-1 ml-2"
                >
                  {isYamlCopied ? (
                    <CheckIcon className="w-4 h-4 text-green-600" />
                  ) : (
                    <CopyIcon className="w-4 h-4" />
                  )}
                </button>
              </Tooltip>
            </div>

            {isYamlExpanded && (
              <YamlCodeBlock value={formatYaml(taskYaml)} readOnly />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

JobSpecSection.propTypes = {
  spec: PropTypes.shape({
    entrypoint: PropTypes.string,
    task_yaml: PropTypes.string,
  }),
};

export function JobDetailPage() {
  const router = useRouter();
  const { cluster, job } = router.query;
  const {
    clusterData,
    clusterJobData,
    loading,
    clusterJobsLoading,
    refreshData,
  } = useClusterDetails({ cluster });
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [isCopied, setIsCopied] = useState(false);
  const [logsRefreshToken, setLogsRefreshToken] = useState(0);
  const [jobSpec, setJobSpec] = useState(null);
  const [jobSpecRefreshToken, setJobSpecRefreshToken] = useState(0);

  const PENDING_STATUSES = useMemo(() => ['INIT', 'PENDING', 'SETTING_UP'], []);

  const isPending = useMemo(() => {
    if (!clusterJobData || !job) return true;
    const jobData = clusterJobData.find((j) => j.id == job);
    return jobData && PENDING_STATUSES.includes(jobData.status);
  }, [clusterJobData, job, PENDING_STATUSES]);

  // Update isInitialLoad when both cluster and job data are first loaded
  useEffect(() => {
    if (!loading && !clusterJobsLoading && isInitialLoad) {
      setIsInitialLoad(false);
    }
  }, [loading, clusterJobsLoading, isInitialLoad]);

  const logStreamArgs = useMemo(
    () => ({
      clusterName: cluster,
      jobId: job,
      workspace: clusterData?.workspace,
    }),
    [cluster, job, clusterData?.workspace]
  );

  // The spec is read from the API server's database, so it is fetched
  // separately from the job queue and does not need the cluster to be UP.
  const clusterLoaded = Boolean(clusterData);
  const clusterWorkspace = clusterData?.workspace;
  useEffect(() => {
    if (!cluster || !job || !clusterLoaded) return undefined;
    let cancelled = false;
    getClusterJobSpec({
      clusterName: cluster,
      jobId: job,
      workspace: clusterWorkspace,
    }).then((spec) => {
      if (!cancelled) setJobSpec(spec);
    });
    return () => {
      cancelled = true;
    };
  }, [cluster, job, clusterLoaded, clusterWorkspace, jobSpecRefreshToken]);

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

  // Scan streamed logs against built-in plus admin-configured URL patterns
  // and surface matches in the Details card as an External Links row.
  const { extractedLinks, scanLines } = useLogLinkExtractor(LINK_SCOPE_JOBS);
  useEffect(() => {
    scanLines(displayLines);
  }, [displayLines, scanLines]);

  // Admin-configured url templates (dashboard.external_links entries with a
  // `url` field) resolved against this job's metadata.
  const jobRecord = useMemo(
    () => clusterJobData?.find((j) => j.id == job),
    [clusterJobData, job]
  );
  const templateLinkContext = useMemo(
    () => ({
      cluster_name: cluster,
      job_id: job,
      job_name: jobRecord?.job,
      user: jobRecord?.user,
      workspace: jobRecord?.workspace,
    }),
    [cluster, job, jobRecord?.job, jobRecord?.user, jobRecord?.workspace]
  );
  const templateLinks = useTemplateLinks(templateLinkContext, LINK_SCOPE_JOBS);

  // Merge order on label collision: template links are the base,
  // server-computed links (extracted from the full log, authoritative)
  // override them, and client-extracted links only fill gaps, so a link
  // surfaces even if the user never streamed the line it appeared on. The
  // merged map is scope-filtered so server-computed links honor entry
  // scopes too.
  const mergedExternalLinks = useMemo(() => {
    const combined = { ...templateLinks, ...(jobRecord?.links || {}) };
    for (const [label, url] of Object.entries(extractedLinks)) {
      if (!(label in combined)) {
        combined[label] = url;
      }
    }
    return combined;
  }, [templateLinks, jobRecord, extractedLinks]);
  const combinedExternalLinks = useScopedLinks(
    mergedExternalLinks,
    LINK_SCOPE_JOBS
  );

  const handleRefreshLogs = () => {
    setLogsRefreshToken((token) => token + 1);
  };

  // Handle manual refresh
  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    setLogsRefreshToken((token) => token + 1);
    setJobSpecRefreshToken((token) => token + 1);
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
        {isInitialLoad && (loading || clusterJobsLoading) ? (
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
                    {Object.keys(combinedExternalLinks).length > 0 && (
                      <div className="col-span-2">
                        <div className="text-gray-600 font-medium text-base">
                          External Links
                        </div>
                        <div className="text-base mt-1">
                          <div className="flex flex-wrap gap-4">
                            {Object.entries(combinedExternalLinks).map(
                              ([label, url]) => {
                                const normalizedUrl = normalizeUrl(url);
                                return (
                                  <a
                                    key={label}
                                    href={normalizedUrl}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-blue-600 hover:text-blue-800 hover:underline"
                                  >
                                    {label}
                                  </a>
                                );
                              }
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                    <JobSpecSection spec={jobSpec} />
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
                    <div className="max-h-[max(200px,calc(100vh_-_572px))] overflow-y-auto">
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
