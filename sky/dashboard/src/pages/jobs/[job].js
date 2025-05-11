import React, { useState, useEffect, useCallback } from 'react';
import { CircularProgress } from '@mui/material';
import { useRouter } from 'next/router';
import { Layout } from '@/components/elements/layout';
import { Card } from '@/components/ui/card';
import { useManagedJobDetails } from '@/data/connectors/jobs';
import Link from 'next/link';
import { RotateCwIcon } from 'lucide-react';
import { CustomTooltip as Tooltip } from '@/components/utils';
import { LogFilter, formatLogs } from '@/components/utils';
import { streamManagedJobLogs } from '@/data/connectors/jobs';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { useMobile } from '@/hooks/useMobile';

function JobDetails() {
  const router = useRouter();
  const { job: jobId, tab } = router.query;
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const { jobData, loading } = useManagedJobDetails(refreshTrigger);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [isLoadingControllerLogs, setIsLoadingControllerLogs] = useState(false);
  const [scrollExecuted, setScrollExecuted] = useState(false);
  const [pageLoaded, setPageLoaded] = useState(false);
  const [domReady, setDomReady] = useState(false);
  const [logsRefreshKey, setLogsRefreshKey] = useState(0);
  const [controllerLogsRefreshKey, setControllerLogsRefreshKey] = useState(0);
  const isMobile = useMobile();
  // Update isInitialLoad when data is first loaded
  React.useEffect(() => {
    if (!loading && isInitialLoad) {
      setIsInitialLoad(false);
    }
  }, [loading, isInitialLoad]);

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
      setLogsRefreshKey((prev) => prev + 1);
      // Trigger controller logs refresh
      setControllerLogsRefreshKey((prev) => prev + 1);
    } catch (error) {
      console.error('Error refreshing data:', error);
    } finally {
      setIsRefreshing(false);
    }
  };

  // Individual refresh handlers for logs
  const handleLogsRefresh = () => {
    setLogsRefreshKey((prev) => prev + 1);
  };

  const handleControllerLogsRefresh = () => {
    setControllerLogsRefreshKey((prev) => prev + 1);
  };

  if (!router.isReady) {
    return <div>Loading...</div>;
  }

  const detailJobData = jobData?.jobs?.find(
    (item) => String(item.id) === String(jobId)
  );

  return (
    <Layout highlighted="jobs">
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
              <div className="p-4">
                <JobDetailsContent
                  key={logsRefreshKey}
                  jobData={detailJobData}
                  activeTab="logs"
                  setIsLoadingLogs={setIsLoadingLogs}
                  setIsLoadingControllerLogs={setIsLoadingControllerLogs}
                  isLoadingLogs={isLoadingLogs}
                  isLoadingControllerLogs={isLoadingControllerLogs}
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
              <div className="p-4">
                <JobDetailsContent
                  key={controllerLogsRefreshKey}
                  jobData={detailJobData}
                  activeTab="controllerlogs"
                  setIsLoadingLogs={setIsLoadingLogs}
                  setIsLoadingControllerLogs={setIsLoadingControllerLogs}
                  isLoadingLogs={isLoadingLogs}
                  isLoadingControllerLogs={isLoadingControllerLogs}
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
    </Layout>
  );
}

function JobDetailsContent({
  jobData,
  activeTab,
  setIsLoadingLogs,
  setIsLoadingControllerLogs,
  isLoadingLogs,
  isLoadingControllerLogs,
}) {
  const [logs, setLogs] = useState([]);
  const [controllerLogs, setControllerLogs] = useState([]);

  const PENDING_STATUSES = ['PENDING', 'SUBMITTED', 'STARTING'];
  const PRE_START_STATUSES = ['PENDING', 'SUBMITTED'];
  const RECOVERING_STATUSES = ['RECOVERING'];

  const isPending = PENDING_STATUSES.includes(jobData.status);
  const isPreStart = PRE_START_STATUSES.includes(jobData.status);
  const isRecovering = RECOVERING_STATUSES.includes(jobData.status);

  // Clear logs when activeTab changes or when jobData.id changes
  useEffect(() => {
    setLogs([]);
  }, [activeTab, jobData.id]);

  useEffect(() => {
    setControllerLogs([]);
  }, [activeTab, jobData.id]);

  // Define a function to handle both log types
  const fetchLogs = useCallback(
    (logType, jobId, setLogs, setIsLoading) => {
      let active = true;
      const controller = new AbortController();

      // Don't fetch logs if job is in pending state
      if (logType === 'logs' && (isPending || isRecovering)) {
        setIsLoading(false);
        return () => {};
      }

      // Don't fetch controller logs if job is in pre-start state
      if (logType === 'controllerlogs' && isPreStart) {
        setIsLoading(false);
        return () => {};
      }

      if (activeTab === logType && jobId) {
        setIsLoading(true);

        streamManagedJobLogs({
          jobId: jobId,
          controller: logType === 'controllerlogs',
          signal: controller.signal,
          onNewLog: (log) => {
            if (active) {
              const strippedLog = formatLogs(log);
              setLogs((prevLogs) => [...prevLogs, strippedLog]);
            }
          },
        })
          .then(() => {
            if (active) {
              setIsLoading(false);
            }
          })
          .catch((error) => {
            if (active) {
              // Only log and handle non-abort errors
              if (error.name !== 'AbortError') {
                console.error(`Error streaming ${logType}:`, error);
                if (error.message) {
                  setLogs((prevLogs) => [
                    ...prevLogs,
                    `Error fetching logs: ${error.message}`,
                  ]);
                }
              }
              setIsLoading(false);
            }
          });

        // Return cleanup function
        return () => {
          active = false;
        };
      }
      return () => {
        active = false;
      };
    },
    [activeTab, isPending, isPreStart, isRecovering]
  );

  // Only fetch logs when actually viewing the logs tab
  useEffect(() => {
    const cleanup = fetchLogs('logs', jobData.id, setLogs, setIsLoadingLogs);
    return cleanup;
  }, [activeTab, jobData.id, fetchLogs, setIsLoadingLogs]);

  // Only fetch controller logs when actually viewing the controller logs tab
  useEffect(() => {
    const cleanup = fetchLogs(
      'controllerlogs',
      jobData.id,
      setControllerLogs,
      setIsLoadingControllerLogs
    );
    return cleanup;
  }, [activeTab, jobData.id, fetchLogs, setIsLoadingControllerLogs]);

  if (activeTab === 'logs') {
    return (
      <div className="max-h-96 overflow-y-auto">
        {isPending ? (
          <div className="bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500">
            <span>
              Waiting for the job to start, please refresh after a while
            </span>
          </div>
        ) : isRecovering ? (
          <div className="bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500">
            <span>
              Waiting for the job to recover, please refresh after a while
            </span>
          </div>
        ) : isLoadingLogs ? (
          <div className="flex items-center justify-center py-4">
            <CircularProgress size={20} className="mr-2" />
            <span>Loading...</span>
          </div>
        ) : (
          <LogFilter logs={logs.join('')} />
        )}
      </div>
    );
  }

  if (activeTab === 'controllerlogs') {
    return (
      <div className="max-h-96 overflow-y-auto">
        {isPreStart ? (
          <div className="bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500">
            <span>
              Waiting for the job controller process to start, please refresh
              after a while
            </span>
          </div>
        ) : isLoadingControllerLogs ? (
          <div className="flex items-center justify-center py-4">
            <CircularProgress size={20} className="mr-2" />
            <span>Loading...</span>
          </div>
        ) : (
          <LogFilter logs={controllerLogs.join('')} controller={true} />
        )}
      </div>
    );
  }

  // Default 'info' tab content
  return (
    <div className="grid grid-cols-2 gap-6">
      <div>
        <div className="text-gray-600 font-medium text-base">Job ID</div>
        <div className="text-base mt-1">{jobData.id}</div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Job Name</div>
        <div className="text-base mt-1">{jobData.name}</div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Status</div>
        <div className="text-base mt-1">
          <StatusBadge status={jobData.status} />
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">User</div>
        <div className="text-base mt-1">{jobData.user}</div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Resources</div>
        <div className="text-base mt-1">{jobData.resources || 'N/A'}</div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Cluster</div>
        <div className="text-base mt-1">{jobData.cluster || '-'}</div>
      </div>
    </div>
  );
}

export default JobDetails;
