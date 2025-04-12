import React, { useState, useEffect } from 'react';
import { CircularProgress } from '@mui/material';
import { useRouter } from 'next/router';
import { Layout } from '@/components/elements/layout';
import { Card } from '@/components/ui/card';
import { EventTable } from '@/components/elements/events';
import { useManagedJobDetails } from '@/data/connectors/jobs';
import { Status2Actions, Status2Icon } from '@/components/jobs';
import Link from 'next/link';
import { RotateCwIcon, FileSearchIcon, SquareIcon } from 'lucide-react';
import { CustomTooltip as Tooltip } from '@/components/utils';
import { LogFilter, formatLogs, contentStyle } from '@/components/jobs';
import { streamManagedJobLogs } from '@/data/connectors/jobs';

function JobDetails() {
  const router = useRouter();
  const { job: jobId, tab } = router.query; // Get tab parameter from URL
  const activeTab = tab || 'info'; // Default to 'info' if no tab specified

  const { jobData, loading } = useManagedJobDetails({});
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [isLoadingControllerLogs, setIsLoadingControllerLogs] = useState(false);
  const [scrollExecuted, setScrollExecuted] = useState(false);
  const [pageLoaded, setPageLoaded] = useState(false);
  const [domReady, setDomReady] = useState(false);

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
      const observer = new MutationObserver((mutations) => {
        // Check if the sections we want to scroll to exist in the DOM
        const logsSection = document.getElementById('logs-section');
        const controllerLogsSection = document.getElementById('controller-logs-section');
        
        if ((tab === 'logs' && logsSection) || (tab === 'controllerlogs' && controllerLogsSection)) {
          setDomReady(true);
          observer.disconnect();
        }
      });

      observer.observe(document.body, {
        childList: true,
        subtree: true
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

  // Handle manual refresh
  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    try {
      // Force a page refresh to reload the data
      router.replace(router.asPath);
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

  // Convert both to strings to ensure proper comparison
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
          <div className="flex items-center space-x-4">
            <Tooltip content="Refresh" className="text-sm text-muted-foreground">
              <button
                onClick={handleManualRefresh}
                disabled={loading || isRefreshing}
                className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center h-8"
              >
                <RotateCwIcon className="w-4 h-4 mr-1.5" />
                <span>Refresh</span>
              </button>
            </Tooltip>
            {detailJobData ? (
              <Status2Actions
                withLabel={true}
                jobParent="/jobs"
                jobId={detailJobData.id}
                jobName={detailJobData.name}
                status={detailJobData.status}
                cluster={detailJobData.cluster}
                managed={true}
              />
            ) : (
              <div className="flex items-center">
                <Tooltip
                  content="View Logs"
                  className="text-sm text-muted-foreground"
                >
                  <button
                    disabled={true}
                    className="text-gray-400 font-medium inline-flex items-center h-8 cursor-not-allowed"
                  >
                    <FileSearchIcon className="w-4 h-4 mr-1.5" />
                    Logs
                  </button>
                </Tooltip>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Display all sections directly on the page */}
      {detailJobData && (
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
              </div>
              <div className="p-4">
                <JobDetailsContent
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
              </div>
              <div className="p-4">
                <JobDetailsContent
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
          if (active && error.name !== 'AbortError') {
            console.error(`Error streaming ${logType}:`, error);
            setIsLoading(false);
            
            // If there was an error, add it to the logs display
            if (error.message) {
              setLogs((prevLogs) => [
                ...prevLogs,
                `Error fetching logs: ${error.message}`,
              ]);
            }
          }
        });
    }
    
    return () => {
      active = false;
      controller.abort();
      console.log(`Cleaning up ${logType} streaming`);
    };
  };

// Use the function for regular logs
useEffect(() => {
  return fetchLogs('logs', jobData.id, setLogs, setIsLoadingLogs);
}, [activeTab, jobData.id, setIsLoadingLogs]);

// Use the function for controller logs
useEffect(() => {
  return fetchLogs('controllerlogs', jobData.id, setControllerLogs, setIsLoadingControllerLogs);
}, [activeTab, jobData.id, setIsLoadingControllerLogs]);

  if (activeTab === 'logs') {
    return (
      <div className="max-h-96 overflow-y-auto" style={contentStyle}>
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
      <div className="max-h-96 overflow-y-auto" style={contentStyle}>
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
