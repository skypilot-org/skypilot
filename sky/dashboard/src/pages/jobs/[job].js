import React, { useState, useEffect } from 'react';
import { CircularProgress } from '@mui/material';
import { useRouter } from 'next/router';
import { Layout } from '@/components/elements/layout';
import { Card } from '@/components/ui/card';
import { useManagedJobDetails } from '@/data/connectors/jobs';
import { Status2Icon } from '@/components/jobs';
import Link from 'next/link';
import { RotateCwIcon } from 'lucide-react';
import { CustomTooltip as Tooltip } from '@/components/utils';
import { LogFilter, formatLogs } from '@/components/jobs';
import { streamManagedJobLogs } from '@/data/connectors/jobs';

function JobDetails() {
  const router = useRouter();
  const { job: jobId, tab } = router.query;
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const { jobData, loading } = useManagedJobDetails(refreshTrigger);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [isLoadingControllerLogs, setIsLoadingControllerLogs] = useState(false);
  const [scrollExecuted, setScrollExecuted] = useState(false);
  const [pageLoaded, setPageLoaded] = useState(false);
  const [domReady, setDomReady] = useState(false);
  const [logsRefreshKey, setLogsRefreshKey] = useState(0);
  const [controllerLogsRefreshKey, setControllerLogsRefreshKey] = useState(0);

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
      setRefreshTrigger(prev => prev + 1);
      // Wait a short time to show the refresh indicator
      await new Promise((resolve) => setTimeout(resolve, 1000));
    } catch (error) {
      console.error('Error refreshing data:', error);
    } finally {
      setIsRefreshing(false);
    }
  };

  // Individual refresh handlers for logs
  const handleLogsRefresh = () => {
    setLogsRefreshKey(prev => prev + 1);
  };

  const handleControllerLogsRefresh = () => {
    setControllerLogsRefreshKey(prev => prev + 1);
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
          {(loading || isRefreshing || isLoadingLogs || isLoadingControllerLogs) && (
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
              <span>Refresh</span>
            </button>
          </Tooltip>
        </div>
      </div>

      {loading ? (
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
                <h3 className="text-lg font-semibold">Logs</h3>
                <Tooltip content="Refresh logs" className="text-muted-foreground">
                  <button
                    onClick={handleLogsRefresh}
                    disabled={isLoadingLogs}
                    className="text-sky-blue hover:text-sky-blue-bright flex items-center"
                  >
                    <RotateCwIcon className={`w-4 h-4 ${isLoadingLogs ? 'animate-spin' : ''}`} />
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
                <h3 className="text-lg font-semibold">Controller Logs</h3>
                <Tooltip content="Refresh controller logs" className="text-muted-foreground">
                  <button
                    onClick={handleControllerLogsRefresh}
                    disabled={isLoadingControllerLogs}
                    className="text-sky-blue hover:text-sky-blue-bright flex items-center"
                  >
                    <RotateCwIcon className={`w-4 h-4 ${isLoadingControllerLogs ? 'animate-spin' : ''}`} />
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
  isLoadingControllerLogs
}) {
  const [logs, setLogs] = useState([]);
  const [controllerLogs, setControllerLogs] = useState([]);

  // Clear logs when activeTab changes or when jobData.id changes
  useEffect(() => {
    setLogs([]);
    console.log('Logs cleared due to tab change or job ID change');
  }, [activeTab, jobData.id]);

  useEffect(() => {
    setControllerLogs([]);
    console.log('Controller logs cleared due to tab change or job ID change');
  }, [activeTab, jobData.id]);

  // Define a function to handle both log types
  const fetchLogs = (logType, jobId, setLogs, setIsLoading) => {
    let active = true;
    const controller = new AbortController();

    if (activeTab === logType && jobId) {
      console.log(`Starting to fetch ${logType} for managed job`, jobId);
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
            console.log(`Finished streaming ${logType}`);
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
  };

  // Only fetch logs when actually viewing the logs tab
  useEffect(() => {
    const cleanup = fetchLogs('logs', jobData.id, setLogs, setIsLoadingLogs);
    return cleanup;
  }, [activeTab, jobData.id]);

  // Only fetch controller logs when actually viewing the controller logs tab
  useEffect(() => {
    const cleanup = fetchLogs(
      'controllerlogs',
      jobData.id,
      setControllerLogs,
      setIsLoadingControllerLogs
    );
    return cleanup;
  }, [activeTab, jobData.id]);

  if (activeTab === 'logs') {
    return (
      <div className="max-h-96 overflow-y-auto">
        {isLoadingLogs ? (
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
        {isLoadingControllerLogs ? (
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
        <div className="text-gray-600 font-medium text-base">User</div>
        <div className="text-base mt-1">{jobData.user}</div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Status</div>
        <div className="text-base mt-1">
          <Status2Icon status={jobData.status} />
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Resources</div>
        <div className="text-base mt-1">{jobData.resources || 'N/A'}</div>
      </div>
      {jobData.cluster && (
        <div>
          <div className="text-gray-600 font-medium text-base">Cluster</div>
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
  );
}

export default JobDetails;
