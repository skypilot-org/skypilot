import React, {
  useState,
  useEffect,
  useRef,
  useMemo,
  useCallback,
} from 'react';
import { CircularProgress } from '@mui/material';
import { useRouter } from 'next/router';
import { Card } from '@/components/ui/card';
import { useSingleManagedJob, getPoolStatus } from '@/data/connectors/jobs';
import Link from 'next/link';
import {
  RotateCwIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  CheckIcon,
  Download,
} from 'lucide-react';
import {
  CustomTooltip as Tooltip,
  formatFullTimestamp,
  renderPoolLink,
} from '@/components/utils';
import { LogFilter } from '@/components/utils';
import {
  streamManagedJobLogs,
  downloadManagedJobLogs,
} from '@/data/connectors/jobs';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { useMobile } from '@/hooks/useMobile';
import Head from 'next/head';
import { NonCapitalizedTooltip } from '@/components/utils';
import { formatJobYaml } from '@/lib/yamlUtils';
import { UserDisplay } from '@/components/elements/UserDisplay';
import { YamlHighlighter } from '@/components/YamlHighlighter';
import dashboardCache from '@/lib/cache';
import { useLogStreamer } from '@/hooks/useLogStreamer';
import PropTypes from 'prop-types';

function JobDetails() {
  const router = useRouter();
  const { job: jobId, tab } = router.query;
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const { jobData, loading } = useSingleManagedJob(jobId, refreshTrigger);
  const [poolsData, setPoolsData] = useState([]);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [isLoadingControllerLogs, setIsLoadingControllerLogs] = useState(false);
  const [scrollExecuted, setScrollExecuted] = useState(false);
  const [pageLoaded, setPageLoaded] = useState(false);
  const [domReady, setDomReady] = useState(false);
  const [refreshLogsFlag, setRefreshLogsFlag] = useState(0);
  const [refreshControllerLogsFlag, setRefreshControllerLogsFlag] = useState(0);
  const isMobile = useMobile();
  // Update isInitialLoad when data is first loaded
  React.useEffect(() => {
    if (!loading && isInitialLoad) {
      setIsInitialLoad(false);
    }
  }, [loading, isInitialLoad]);

  // Fetch pools data for hash comparison
  useEffect(() => {
    async function fetchPoolsData() {
      try {
        const poolsResponse = await dashboardCache.get(getPoolStatus, [{}]);
        setPoolsData(poolsResponse.pools || []);
      } catch (error) {
        console.error('Error fetching pools data:', error);
        setPoolsData([]);
      }
    }
    fetchPoolsData();
  }, []);

  // Function to scroll to a specific section
  const scrollToSection = (sectionId) => {
    const element = document.getElementById(sectionId);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth' });
    }
  };

  // Set pageLoaded to true when the component mounts
  useEffect(() => {
    setPageLoaded(true);
  }, []);

  // Use MutationObserver to detect when the DOM is fully rendered
  useEffect(() => {
    if (!domReady) {
      const observer = new MutationObserver(() => {
        // Check if the sections we want to scroll to exist in the DOM
        const logsSection = document.getElementById('logs-section');
        const controllerLogsSection = document.getElementById(
          'controller-logs-section'
        );

        if (
          (tab === 'logs' && logsSection) ||
          (tab === 'controllerlogs' && controllerLogsSection)
        ) {
          setDomReady(true);
          observer.disconnect();
        }
      });

      observer.observe(document.body, {
        childList: true,
        subtree: true,
      });

      return () => observer.disconnect();
    }
  }, [domReady, tab]);

  // Scroll to the appropriate section when the page loads with a tab parameter
  useEffect(() => {
    if (router.isReady && pageLoaded && domReady && !scrollExecuted) {
      // Add a small delay to ensure the DOM is fully rendered
      const timer = setTimeout(() => {
        if (tab === 'logs') {
          scrollToSection('logs-section');
          setScrollExecuted(true);
        } else if (tab === 'controllerlogs') {
          scrollToSection('controller-logs-section');
          setScrollExecuted(true);
        }
      }, 800);

      return () => clearTimeout(timer);
    }
  }, [router.isReady, tab, scrollExecuted, pageLoaded, domReady]);

  // Reset scrollExecuted when tab changes
  useEffect(() => {
    setScrollExecuted(false);
    setDomReady(false);
  }, [tab]);

  // Handle manual refresh of everything
  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    try {
      // Trigger job data refresh
      setRefreshTrigger((prev) => prev + 1);
      // Trigger logs refresh
      setRefreshLogsFlag((prev) => prev + 1);
      // Trigger controller logs refresh
      setRefreshControllerLogsFlag((prev) => prev + 1);
    } catch (error) {
      console.error('Error refreshing data:', error);
    } finally {
      setIsRefreshing(false);
    }
  };

  // Individual refresh handlers for logs
  const handleLogsRefresh = () => {
    setRefreshLogsFlag((prev) => prev + 1);
  };

  const handleControllerLogsRefresh = () => {
    setRefreshControllerLogsFlag((prev) => prev + 1);
  };

  if (!router.isReady) {
    return <div>Loading...</div>;
  }

  const detailJobData = jobData?.jobs?.find(
    (item) => String(item.id) === String(jobId)
  );

  const title = jobId
    ? `Job: ${jobId} | SkyPilot Dashboard`
    : 'Job Details | SkyPilot Dashboard';

  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <>
        <div className="flex items-center justify-between mb-4">
          <div className="text-base flex items-center">
            <Link href="/jobs" className="text-sky-blue hover:underline">
              Managed Jobs
            </Link>
            <span className="mx-2 text-gray-500">›</span>
            <Link
              href={`/jobs/${jobId}`}
              className="text-sky-blue hover:underline"
            >
              {jobId} {detailJobData?.name ? `(${detailJobData.name})` : ''}
            </Link>
          </div>

          <div className="text-sm flex items-center">
            {(loading ||
              isRefreshing ||
              isLoadingLogs ||
              isLoadingControllerLogs) && (
              <div className="flex items-center mr-4">
                <CircularProgress size={15} className="mt-0" />
                <span className="ml-2 text-gray-500">Loading...</span>
              </div>
            )}
            <Tooltip content="Refresh" className="text-muted-foreground">
              <button
                onClick={handleManualRefresh}
                disabled={loading || isRefreshing}
                className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center h-8"
              >
                <RotateCwIcon className="w-4 h-4 mr-1.5" />
                {!isMobile && <span>Refresh</span>}
              </button>
            </Tooltip>
          </div>
        </div>

        {loading && isInitialLoad ? (
          <div className="flex items-center justify-center py-32">
            <CircularProgress size={20} className="mr-2" />
            <span>Loading...</span>
          </div>
        ) : detailJobData ? (
          <div className="space-y-8">
            {/* Details Section */}
            <div id="details-section">
              <Card>
                <div className="flex items-center justify-between px-4 pt-4">
                  <h3 className="text-lg font-semibold">Details</h3>
                </div>
                <div className="p-4">
                  <JobDetailsContent
                    jobData={detailJobData}
                    activeTab="info"
                    setIsLoadingLogs={setIsLoadingLogs}
                    setIsLoadingControllerLogs={setIsLoadingControllerLogs}
                    isLoadingLogs={isLoadingLogs}
                    isLoadingControllerLogs={isLoadingControllerLogs}
                    refreshFlag={0}
                    poolsData={poolsData}
                  />
                </div>
              </Card>
            </div>

            {/* Logs Section */}
            <div id="logs-section" className="mt-6">
              <Card>
                <div className="flex items-center justify-between px-4 pt-4">
                  <div className="flex items-center">
                    <h3 className="text-lg font-semibold">Logs</h3>
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
                          downloadManagedJobLogs({
                            jobId: parseInt(
                              Array.isArray(jobId) ? jobId[0] : jobId
                            ),
                            controller: false,
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
                        onClick={handleLogsRefresh}
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
                  <JobDetailsContent
                    jobData={detailJobData}
                    activeTab="logs"
                    setIsLoadingLogs={setIsLoadingLogs}
                    setIsLoadingControllerLogs={setIsLoadingControllerLogs}
                    isLoadingLogs={isLoadingLogs}
                    isLoadingControllerLogs={isLoadingControllerLogs}
                    refreshFlag={refreshLogsFlag}
                    poolsData={poolsData}
                  />
                </div>
              </Card>
            </div>

            {/* Controller Logs Section */}
            <div id="controller-logs-section" className="mt-6">
              <Card>
                <div className="flex items-center justify-between px-4 pt-4">
                  <div className="flex items-center">
                    <h3 className="text-lg font-semibold">Controller Logs</h3>
                    <span className="ml-2 text-xs text-gray-500">
                      (Logs are not streaming; click refresh to fetch the latest
                      logs.)
                    </span>
                  </div>
                  <div className="flex items-center space-x-3">
                    <Tooltip
                      content="Download full controller logs"
                      className="text-muted-foreground"
                    >
                      <button
                        onClick={() =>
                          downloadManagedJobLogs({
                            jobId: parseInt(
                              Array.isArray(jobId) ? jobId[0] : jobId
                            ),
                            controller: true,
                          })
                        }
                        className="text-sky-blue hover:text-sky-blue-bright flex items-center"
                      >
                        <Download className="w-4 h-4" />
                      </button>
                    </Tooltip>
                    <Tooltip
                      content="Refresh controller logs"
                      className="text-muted-foreground"
                    >
                      <button
                        onClick={handleControllerLogsRefresh}
                        disabled={isLoadingControllerLogs}
                        className="text-sky-blue hover:text-sky-blue-bright flex items-center"
                      >
                        <RotateCwIcon
                          className={`w-4 h-4 ${isLoadingControllerLogs ? 'animate-spin' : ''}`}
                        />
                      </button>
                    </Tooltip>
                  </div>
                </div>
                <div className="p-4">
                  <JobDetailsContent
                    jobData={detailJobData}
                    activeTab="controllerlogs"
                    setIsLoadingLogs={setIsLoadingLogs}
                    setIsLoadingControllerLogs={setIsLoadingControllerLogs}
                    isLoadingLogs={isLoadingLogs}
                    isLoadingControllerLogs={isLoadingControllerLogs}
                    refreshFlag={refreshControllerLogsFlag}
                    poolsData={poolsData}
                  />
                </div>
              </Card>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center py-32">
            <span>Job not found</span>
          </div>
        )}
      </>
    </>
  );
}

function JobDetailsContent({
  jobData,
  activeTab,
  setIsLoadingLogs,
  setIsLoadingControllerLogs,
  isLoadingLogs,
  isLoadingControllerLogs,
  refreshFlag,
  poolsData,
}) {
  const [isYamlExpanded, setIsYamlExpanded] = useState(false);
  const [expandedYamlDocs, setExpandedYamlDocs] = useState({});
  const [isCopied, setIsCopied] = useState(false);
  const [isCommandCopied, setIsCommandCopied] = useState(false);

  // Auto-scroll refs
  const logsContainerRef = useRef(null);
  const controllerLogsContainerRef = useRef(null);

  // Custom hook for auto-scrolling
  const scrollToBottom = useCallback((logType) => {
    const containerRef =
      logType === 'logs' ? logsContainerRef : controllerLogsContainerRef;

    if (!containerRef.current) return;

    // Try multiple ways to find the scrollable container
    const attempts = [
      () => containerRef.current.querySelector('.logs-container'),
      () => containerRef.current.querySelector('[class*="logs-container"]'),
      () => containerRef.current.querySelector('div[style*="overflow"]'),
      () => containerRef.current, // Fallback to the ref itself
    ];

    for (const attempt of attempts) {
      const container = attempt();
      if (container && container.scrollHeight > container.clientHeight) {
        container.scrollTop = container.scrollHeight;
        console.log(`Auto-scrolled ${logType} to bottom`); // Debug log
        break;
      }
    }
  }, []);

  const PENDING_STATUSES = ['PENDING', 'SUBMITTED', 'STARTING'];
  const PRE_START_STATUSES = ['PENDING', 'SUBMITTED'];
  const RECOVERING_STATUSES = ['RECOVERING'];

  const isPending = PENDING_STATUSES.includes(jobData.status);
  const isPreStart = PRE_START_STATUSES.includes(jobData.status);
  const isRecovering = RECOVERING_STATUSES.includes(jobData.status);

  const toggleYamlExpanded = () => {
    setIsYamlExpanded(!isYamlExpanded);
  };

  const toggleYamlDocExpanded = (index) => {
    setExpandedYamlDocs((prev) => ({
      ...prev,
      [index]: !prev[index],
    }));
  };

  const copyYamlToClipboard = async () => {
    try {
      const yamlDocs = formatJobYaml(jobData.dag_yaml);
      let textToCopy = '';

      if (yamlDocs.length === 1) {
        // Single document - use the formatted content directly
        textToCopy = yamlDocs[0].content;
      } else if (yamlDocs.length > 1) {
        // Multiple documents - join them with document separators
        textToCopy = yamlDocs.map((doc) => doc.content).join('\n---\n');
      } else {
        // Fallback to raw YAML if formatting fails
        textToCopy = jobData.dag_yaml;
      }

      await navigator.clipboard.writeText(textToCopy);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000); // Reset after 2 seconds
    } catch (err) {
      console.error('Failed to copy YAML to clipboard:', err);
    }
  };

  const copyCommandToClipboard = async () => {
    try {
      await navigator.clipboard.writeText(jobData.entrypoint);
      setIsCommandCopied(true);
      setTimeout(() => setIsCommandCopied(false), 2000); // Reset after 2 seconds
    } catch (err) {
      console.error('Failed to copy command to clipboard:', err);
    }
  };

  const logStreamArgs = useMemo(
    () => ({
      jobId: jobData.id,
      controller: false,
    }),
    [jobData.id]
  );

  const controllerStreamArgs = useMemo(
    () => ({
      jobId: jobData.id,
      controller: true,
    }),
    [jobData.id]
  );

  const handleLogsError = useCallback((error) => {
    console.error('Error streaming logs:', error);
  }, []);

  const handleControllerLogsError = useCallback((error) => {
    console.error('Error streaming controller logs:', error);
  }, []);

  const {
    lines: logs,
    isLoading: streamingLogsLoading,
    hasReceivedFirstChunk: hasReceivedLogChunk,
  } = useLogStreamer({
    streamFn: streamManagedJobLogs,
    streamArgs: logStreamArgs,
    enabled: activeTab === 'logs' && !isPending && !isRecovering,
    refreshTrigger: activeTab === 'logs' ? refreshFlag : 0,
    onError: handleLogsError,
  });

  const {
    lines: controllerLogs,
    isLoading: streamingControllerLogsLoading,
    hasReceivedFirstChunk: hasReceivedControllerChunk,
  } = useLogStreamer({
    streamFn: streamManagedJobLogs,
    streamArgs: controllerStreamArgs,
    enabled: activeTab === 'controllerlogs' && !isPreStart,
    refreshTrigger: activeTab === 'controllerlogs' ? refreshFlag : 0,
    onError: handleControllerLogsError,
  });

  useEffect(() => {
    setIsLoadingLogs(streamingLogsLoading);
  }, [streamingLogsLoading, setIsLoadingLogs]);

  useEffect(() => {
    setIsLoadingControllerLogs(streamingControllerLogsLoading);
  }, [streamingControllerLogsLoading, setIsLoadingControllerLogs]);

  // Auto-scroll to bottom when logs change or tab changes
  useEffect(() => {
    const performScroll = () => {
      if (
        (activeTab === 'logs' && logs.length) ||
        (activeTab === 'controllerlogs' && controllerLogs.length)
      ) {
        scrollToBottom(activeTab === 'logs' ? 'logs' : 'controllerlogs');
      }
    };

    // Use requestAnimationFrame for better timing after DOM updates
    requestAnimationFrame(() => {
      requestAnimationFrame(performScroll); // Double RAF to ensure DOM is updated
    });
  }, [activeTab, logs, controllerLogs, scrollToBottom]);

  if (activeTab === 'logs') {
    return (
      <div className="max-h-96 overflow-y-auto" ref={logsContainerRef}>
        {isPending ? (
          <div className="bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500">
            <span>Waiting for the job to start; refresh in a few moments.</span>
          </div>
        ) : isRecovering ? (
          <div className="bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500">
            <span>
              Waiting for the job to recover; refresh in a few moments.
            </span>
          </div>
        ) : hasReceivedLogChunk || logs.length ? (
          <LogFilter logs={logs} />
        ) : isLoadingLogs ? (
          <div className="flex items-center justify-center py-4">
            <CircularProgress size={20} className="mr-2" />
            <span>Loading logs...</span>
          </div>
        ) : (
          <LogFilter logs={logs} />
        )}
      </div>
    );
  }

  if (activeTab === 'controllerlogs') {
    return (
      <div
        className="max-h-96 overflow-y-auto"
        ref={controllerLogsContainerRef}
      >
        {isPreStart ? (
          <div className="bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500">
            <span>
              Waiting for the job controller process to start; refresh in a few
              moments.
            </span>
          </div>
        ) : hasReceivedControllerChunk || controllerLogs.length ? (
          <LogFilter logs={controllerLogs} controller={true} />
        ) : isLoadingControllerLogs ? (
          <div className="flex items-center justify-center py-4">
            <CircularProgress size={20} className="mr-2" />
            <span>Loading logs...</span>
          </div>
        ) : (
          <LogFilter logs={controllerLogs} controller={true} />
        )}
      </div>
    );
  }

  // Default 'info' tab content
  return (
    <div className="grid grid-cols-2 gap-6">
      <div>
        <div className="text-gray-600 font-medium text-base">Job ID (Name)</div>
        <div className="text-base mt-1">
          {jobData.id} {jobData.name ? `(${jobData.name})` : ''}
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Status</div>
        <div className="text-base mt-1">
          <StatusBadge status={jobData.status} />
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">User</div>
        <div className="text-base mt-1">
          <UserDisplay username={jobData.user} userHash={jobData.user_hash} />
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Workspace</div>
        <div className="text-base mt-1">
          <Link
            href="/workspaces"
            className="text-gray-700 hover:text-blue-600 hover:underline"
          >
            {jobData.workspace || 'default'}
          </Link>
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Submitted</div>
        <div className="text-base mt-1">
          {jobData.submitted_at
            ? formatFullTimestamp(jobData.submitted_at)
            : 'N/A'}
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">
          Requested Resources
        </div>
        <div className="text-base mt-1">
          {jobData.requested_resources || 'N/A'}
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Infra</div>
        <div className="text-base mt-1">
          {jobData.infra ? (
            <NonCapitalizedTooltip
              content={jobData.full_infra || jobData.infra}
              className="text-sm text-muted-foreground"
            >
              <span>
                <Link href="/infra" className="text-blue-600 hover:underline">
                  {jobData.cloud || jobData.infra.split('(')[0].trim()}
                </Link>
                {jobData.infra.includes('(') && (
                  <span>
                    {' ' + jobData.infra.substring(jobData.infra.indexOf('('))}
                  </span>
                )}
              </span>
            </NonCapitalizedTooltip>
          ) : (
            '-'
          )}
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Resources</div>
        <div className="text-base mt-1">
          {jobData.resources_str_full || jobData.resources_str || '-'}
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Git Commit</div>
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
                    await navigator.clipboard.writeText(jobData.git_commit);
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

      <div>
        <div className="text-gray-600 font-medium text-base">Pool</div>
        <div className="text-base mt-1">
          {renderPoolLink(jobData.pool, jobData.pool_hash, poolsData)}
        </div>
      </div>

      {/* Entrypoint section - spans both columns */}
      {(jobData.entrypoint || jobData.dag_yaml) && (
        <div className="col-span-2">
          <div className="flex items-center">
            <div className="text-gray-600 font-medium text-base">
              Entrypoint
            </div>
            {jobData.entrypoint && (
              <Tooltip
                content={isCommandCopied ? 'Copied!' : 'Copy command'}
                className="text-muted-foreground"
              >
                <button
                  onClick={copyCommandToClipboard}
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
            {/* Launch Command */}
            {jobData.entrypoint && (
              <div>
                <div className="bg-gray-50 border border-gray-200 rounded-md p-3">
                  <code className="text-sm text-gray-800 font-mono break-all">
                    {jobData.entrypoint}
                  </code>
                </div>
              </div>
            )}

            {/* Task YAML - Collapsible */}
            {jobData.dag_yaml && jobData.dag_yaml !== '{}' && (
              <div>
                <div className="flex items-center mb-2">
                  <button
                    onClick={toggleYamlExpanded}
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
                    content={isCopied ? 'Copied!' : 'Copy YAML'}
                    className="text-muted-foreground"
                  >
                    <button
                      onClick={copyYamlToClipboard}
                      className="flex items-center text-gray-500 hover:text-gray-700 transition-colors duration-200 p-1 ml-2"
                    >
                      {isCopied ? (
                        <CheckIcon className="w-4 h-4 text-green-600" />
                      ) : (
                        <CopyIcon className="w-4 h-4" />
                      )}
                    </button>
                  </Tooltip>
                </div>

                {isYamlExpanded && (
                  <div className="bg-gray-50 border border-gray-200 rounded-md p-3 max-h-96 overflow-y-auto">
                    {(() => {
                      const yamlDocs = formatJobYaml(jobData.dag_yaml);
                      if (yamlDocs.length === 0) {
                        return (
                          <div className="text-gray-500">No YAML available</div>
                        );
                      } else if (yamlDocs.length === 1) {
                        // Single document - show directly
                        return (
                          <YamlHighlighter className="whitespace-pre-wrap">
                            {yamlDocs[0].content}
                          </YamlHighlighter>
                        );
                      } else {
                        // Multiple documents - show with collapsible sections
                        return (
                          <div className="space-y-4">
                            {yamlDocs.map((doc, index) => (
                              <div
                                key={index}
                                className="border-b border-gray-200 pb-4 last:border-b-0"
                              >
                                <button
                                  onClick={() => toggleYamlDocExpanded(index)}
                                  className="flex items-center justify-between w-full text-left focus:outline-none"
                                >
                                  <div className="flex items-center">
                                    {expandedYamlDocs[index] ? (
                                      <ChevronDownIcon className="w-4 h-4 mr-2" />
                                    ) : (
                                      <ChevronRightIcon className="w-4 h-4 mr-2" />
                                    )}
                                    <span className="text-sm font-medium text-gray-700">
                                      Task {index + 1}: {doc.preview}
                                    </span>
                                  </div>
                                </button>
                                {expandedYamlDocs[index] && (
                                  <div className="mt-3 ml-6">
                                    <YamlHighlighter className="whitespace-pre-wrap">
                                      {doc.content}
                                    </YamlHighlighter>
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        );
                      }
                    })()}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

JobDetailsContent.propTypes = {
  jobData: PropTypes.shape({
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    name: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    status: PropTypes.string,
    user: PropTypes.string,
    user_hash: PropTypes.string,
    workspace: PropTypes.string,
    submitted_at: PropTypes.oneOfType([
      PropTypes.string,
      PropTypes.number,
      PropTypes.instanceOf(Date),
    ]),
    requested_resources: PropTypes.string,
    infra: PropTypes.string,
    full_infra: PropTypes.string,
    cloud: PropTypes.string,
    resources_str_full: PropTypes.string,
    resources_str: PropTypes.string,
    git_commit: PropTypes.string,
    pool: PropTypes.string,
    pool_hash: PropTypes.string,
    entrypoint: PropTypes.string,
    dag_yaml: PropTypes.string,
  }),
  activeTab: PropTypes.string,
  setIsLoadingLogs: PropTypes.func,
  setIsLoadingControllerLogs: PropTypes.func,
  isLoadingLogs: PropTypes.bool,
  isLoadingControllerLogs: PropTypes.bool,
  refreshFlag: PropTypes.number,
  poolsData: PropTypes.array,
};

export default JobDetails;
