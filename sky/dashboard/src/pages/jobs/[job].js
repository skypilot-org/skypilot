import React, { useState, useEffect, useCallback, useRef } from 'react';
import { CircularProgress } from '@mui/material';
import { useRouter } from 'next/router';
import { Layout } from '@/components/elements/layout';
import { Card } from '@/components/ui/card';
import { useSingleManagedJob } from '@/data/connectors/jobs';
import Link from 'next/link';
import {
  RotateCwIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  CheckIcon,
} from 'lucide-react';
import {
  CustomTooltip as Tooltip,
  formatFullTimestamp,
} from '@/components/utils';
import { LogFilter, formatLogs, stripAnsiCodes } from '@/components/utils';
import { streamManagedJobLogs } from '@/data/connectors/jobs';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { useMobile } from '@/hooks/useMobile';
import Head from 'next/head';
import { NonCapitalizedTooltip } from '@/components/utils';
import { formatJobYaml } from '@/lib/yamlUtils';
import { UserDisplay } from '@/components/elements/UserDisplay';
import { YamlHighlighter } from '@/components/YamlHighlighter';

function JobDetails() {
  const router = useRouter();
  const { job: jobId, tab } = router.query;
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const { jobData, loading } = useSingleManagedJob(jobId, refreshTrigger);
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
                    jobData={detailJobData}
                    activeTab="logs"
                    setIsLoadingLogs={setIsLoadingLogs}
                    setIsLoadingControllerLogs={setIsLoadingControllerLogs}
                    isLoadingLogs={isLoadingLogs}
                    isLoadingControllerLogs={isLoadingControllerLogs}
                    refreshFlag={refreshLogsFlag}
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
                    jobData={detailJobData}
                    activeTab="controllerlogs"
                    setIsLoadingLogs={setIsLoadingLogs}
                    setIsLoadingControllerLogs={setIsLoadingControllerLogs}
                    isLoadingLogs={isLoadingLogs}
                    isLoadingControllerLogs={isLoadingControllerLogs}
                    refreshFlag={refreshControllerLogsFlag}
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
}) {
  // Change from array to string for better performance
  const [logs, setLogs] = useState('');
  const [controllerLogs, setControllerLogs] = useState('');
  const [isYamlExpanded, setIsYamlExpanded] = useState(false);
  const [expandedYamlDocs, setExpandedYamlDocs] = useState({});
  const [isCopied, setIsCopied] = useState(false);
  const [isCommandCopied, setIsCommandCopied] = useState(false);

  // Add state for tracking incremental refresh
  const [currentLogLength, setCurrentLogLength] = useState(0);
  const [currentControllerLogLength, setCurrentControllerLogLength] =
    useState(0);
  const [isRefreshingLogs, setIsRefreshingLogs] = useState(false);
  const [isRefreshingControllerLogs, setIsRefreshingControllerLogs] =
    useState(false);

  // Add state for streaming indicators
  const [hasStartedLoading, setHasStartedLoading] = useState(false);
  const [hasReceivedFirstChunk, setHasReceivedFirstChunk] = useState(false);

  // Add refs to track active requests and prevent duplicates
  const activeLogsRequest = useRef(null);
  const activeControllerLogsRequest = useRef(null);

  // Track aborted controllers to prevent double-abort
  const abortedControllers = useRef(new WeakSet());

  // Auto-scroll refs
  const logsContainerRef = useRef(null);
  const controllerLogsContainerRef = useRef(null);

  // Performance optimization refs
  const logsBatchRef = useRef('');
  const controllerLogsBatchRef = useRef('');
  const updateTimeoutRef = useRef(null);
  const lastUpdateTimeRef = useRef(0);

  // Configuration for performance
  const BATCH_UPDATE_INTERVAL = 100; // Batch updates every 100ms
  const THROTTLE_INTERVAL = 50; // Minimum time between updates

  // Helper function to safely abort an AbortController
  const safeAbort = useCallback((controller, description = 'request') => {
    if (!controller) return;

    // Check if we've already aborted this controller
    if (abortedControllers.current.has(controller)) {
      console.log(
        `${description} controller already aborted previously, skipping`
      );
      return;
    }

    try {
      // Additional safety checks
      if (typeof controller.abort !== 'function') {
        console.warn(
          `Controller for ${description} does not have abort method`
        );
        return;
      }

      // Check if already aborted via signal
      if (controller.signal && controller.signal.aborted) {
        console.log(`${description} already aborted via signal, skipping`);
        abortedControllers.current.add(controller); // Mark as aborted
        return;
      }

      // Attempt to abort
      controller.abort();
      abortedControllers.current.add(controller); // Mark as aborted
      console.log(`Successfully aborted ${description}`);
    } catch (error) {
      // Handle any type of error that might occur during abort
      console.log(
        `Caught error while aborting ${description}:`,
        error.name,
        error.message
      );

      // Mark as aborted even if there was an error to prevent retry
      abortedControllers.current.add(controller);

      // Only warn for unexpected errors (not AbortError or InvalidStateError)
      if (error.name !== 'AbortError' && error.name !== 'InvalidStateError') {
        console.warn(`Unexpected error aborting ${description}:`, error);
      }
    }
  }, []);

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

  // Clear logs when activeTab changes or when jobData.id changes
  useEffect(() => {
    setLogs('');
    setCurrentLogLength(0);
    setHasReceivedFirstChunk(false);
  }, [activeTab, jobData.id]);

  useEffect(() => {
    setControllerLogs('');
    setCurrentControllerLogLength(0);
    setHasReceivedFirstChunk(false);
  }, [activeTab, jobData.id]);

  // Define a function to handle both log types with simplified logic
  const fetchLogs = useCallback(
    (logType, jobId, setLogs, setIsLoading, isRefreshing = false) => {
      // Check if there's already an active request for this log type
      const activeRequestRef =
        logType === 'logs' ? activeLogsRequest : activeControllerLogsRequest;

      if (activeRequestRef.current) {
        console.log(`Request already active for ${logType}, skipping...`);
        return () => {};
      }

      let active = true;
      const controller = new AbortController();

      // Don't fetch logs if job is in pending state
      if (logType === 'logs' && (isPending || isRecovering)) {
        setIsLoading(false);
        if (isRefreshing) {
          setIsRefreshingLogs(false);
        }
        return () => {};
      }

      // Don't fetch controller logs if job is in pre-start state
      if (logType === 'controllerlogs' && isPreStart) {
        setIsLoading(false);
        if (isRefreshing) {
          setIsRefreshingControllerLogs(false);
        }
        return () => {};
      }

      if (jobId) {
        // Mark request as active
        activeRequestRef.current = controller;

        setIsLoading(true);
        setHasStartedLoading(true);

        // If refreshing, clear and restart
        if (isRefreshing) {
          setLogs('');
          if (logType === 'logs') {
            setCurrentLogLength(0);
          } else {
            setCurrentControllerLogLength(0);
          }
        }

        streamManagedJobLogs({
          jobId: jobId,
          controller: logType === 'controllerlogs',
          signal: controller.signal,
          onNewLog: (chunk) => {
            if (active) {
              // Set first chunk received flag for immediate display
              if (!hasReceivedFirstChunk) {
                setHasReceivedFirstChunk(true);
              }

              const setLogsFunction =
                logType === 'logs' ? setLogs : setControllerLogs;

              setLogsFunction((prevLogs) => {
                // Split the chunk into lines
                const newLines = chunk
                  .split('\n')
                  .filter((line) => line.trim());

                let updatedLogs = prevLogs;

                for (const line of newLines) {
                  // Clean the line (remove ANSI codes)
                  const cleanLine = stripAnsiCodes(line);

                  // Check if this is a progress bar line
                  const isProgressBar = /\d+%\s*\|/.test(cleanLine);

                  if (isProgressBar) {
                    // Extract process identifier from the new line
                    const processMatch = cleanLine.match(/^\(([^)]+)\)/);

                    if (processMatch && updatedLogs) {
                      // Look for the last progress bar from the same process in existing logs
                      const existingLines = updatedLogs.split('\n');
                      let replaced = false;

                      // Search from the end for efficiency
                      for (let i = existingLines.length - 1; i >= 0; i--) {
                        const existingLine = existingLines[i];
                        if (/\d+%\s*\|/.test(existingLine)) {
                          const existingProcessMatch =
                            existingLine.match(/^\(([^)]+)\)/);
                          if (
                            existingProcessMatch &&
                            existingProcessMatch[1] === processMatch[1]
                          ) {
                            // Found a progress bar from the same process, replace it
                            existingLines[i] = cleanLine;
                            updatedLogs = existingLines.join('\n');
                            replaced = true;
                            break;
                          }
                        }
                      }

                      if (!replaced) {
                        // No existing progress bar from this process, append
                        updatedLogs += (updatedLogs ? '\n' : '') + cleanLine;
                      }
                    } else {
                      // First line or no process match, just append
                      updatedLogs += (updatedLogs ? '\n' : '') + cleanLine;
                    }
                  } else {
                    // Regular log line, just append
                    updatedLogs += (updatedLogs ? '\n' : '') + cleanLine;
                  }
                }

                // Update length tracking
                if (logType === 'logs') {
                  setCurrentLogLength(updatedLogs.length);
                } else {
                  setCurrentControllerLogLength(updatedLogs.length);
                }

                return updatedLogs;
              });

              // Auto-scroll after update
              requestAnimationFrame(() => {
                scrollToBottom(logType);
              });
            }
          },
        })
          .then(() => {
            if (active) {
              setIsLoading(false);
              if (isRefreshing) {
                if (logType === 'logs') {
                  setIsRefreshingLogs(false);
                } else {
                  setIsRefreshingControllerLogs(false);
                }
              }

              // Final scroll to bottom when loading completes
              requestAnimationFrame(() => {
                scrollToBottom(logType);
              });
            }
          })
          .catch((error) => {
            if (active) {
              // Only log and handle non-abort errors
              if (error.name !== 'AbortError') {
                console.error(`Error streaming ${logType}:`, error);
                if (error.message) {
                  setLogs(
                    (prevLogs) =>
                      prevLogs + `Error fetching logs: ${error.message}\n`
                  );
                }
              }
              setIsLoading(false);
              if (isRefreshing) {
                if (logType === 'logs') {
                  setIsRefreshingLogs(false);
                } else {
                  setIsRefreshingControllerLogs(false);
                }
              }
            }
          })
          .finally(() => {
            console.log(`Cleaning up ${logType} request`);
            active = false;

            // Safely abort the controller if it matches the current one
            if (activeRequestRef.current === controller) {
              safeAbort(controller, logType);
              activeRequestRef.current = null;
            }

            // Clear any pending batch updates for this log type
            if (logType === 'logs') {
              logsBatchRef.current = '';
            } else {
              controllerLogsBatchRef.current = '';
            }

            // Clear update timeout
            if (updateTimeoutRef.current) {
              clearTimeout(updateTimeoutRef.current);
              updateTimeoutRef.current = null;
            }
          });

        // Return cleanup function
        return () => {
          console.log(`Cleaning up ${logType} request`);
          active = false;

          // Safely abort the controller if it matches the current one and hasn't been cleaned up yet
          if (activeRequestRef.current === controller) {
            safeAbort(controller, `${logType} cleanup`);
            activeRequestRef.current = null;
          }

          // Clear any pending batch updates for this log type
          if (logType === 'logs') {
            logsBatchRef.current = '';
          } else {
            controllerLogsBatchRef.current = '';
          }

          // Clear update timeout
          if (updateTimeoutRef.current) {
            clearTimeout(updateTimeoutRef.current);
            updateTimeoutRef.current = null;
          }
        };
      }
      return () => {
        active = false;
      };
    },
    [
      isPending,
      isPreStart,
      isRecovering,
      hasReceivedFirstChunk,
      safeAbort,
      scrollToBottom,
    ]
  );

  // Fetch both logs and controller logs in parallel, regardless of activeTab
  useEffect(() => {
    // Cancel any existing request
    if (activeLogsRequest.current) {
      safeAbort(activeLogsRequest.current, 'logs');
      activeLogsRequest.current = null;
    }

    // Only fetch if not in pending/recovering state
    if (!isPending && !isRecovering) {
      const cleanup = fetchLogs('logs', jobData.id, setLogs, setIsLoadingLogs);
      return cleanup;
    }
  }, [
    jobData.id,
    fetchLogs,
    setIsLoadingLogs,
    isPending,
    isRecovering,
    safeAbort,
  ]);

  useEffect(() => {
    // Cancel any existing request
    if (activeControllerLogsRequest.current) {
      safeAbort(activeControllerLogsRequest.current, 'controller logs');
      activeControllerLogsRequest.current = null;
    }

    // Only fetch if not in pre-start state
    if (!isPreStart) {
      const cleanup = fetchLogs(
        'controllerlogs',
        jobData.id,
        setControllerLogs,
        setIsLoadingControllerLogs
      );
      return cleanup;
    }
  }, [
    jobData.id,
    fetchLogs,
    setIsLoadingControllerLogs,
    isPreStart,
    safeAbort,
  ]);

  // Handle refresh for logs
  useEffect(() => {
    if (refreshFlag > 0 && activeTab === 'logs') {
      // Cancel any existing request
      if (activeLogsRequest.current) {
        safeAbort(activeLogsRequest.current, 'logs refresh');
        activeLogsRequest.current = null;
      }

      setIsRefreshingLogs(true);
      const cleanup = fetchLogs(
        'logs',
        jobData.id,
        setLogs,
        setIsLoadingLogs,
        true // isRefreshing = true
      );
      return cleanup;
    }
  }, [
    refreshFlag,
    activeTab,
    jobData.id,
    fetchLogs,
    setIsLoadingLogs,
    safeAbort,
  ]);

  // Handle refresh for controller logs
  useEffect(() => {
    if (refreshFlag > 0 && activeTab === 'controllerlogs') {
      // Cancel any existing request
      if (activeControllerLogsRequest.current) {
        safeAbort(
          activeControllerLogsRequest.current,
          'controller logs refresh'
        );
        activeControllerLogsRequest.current = null;
      }

      setIsRefreshingControllerLogs(true);
      const cleanup = fetchLogs(
        'controllerlogs',
        jobData.id,
        setControllerLogs,
        setIsLoadingControllerLogs,
        true // isRefreshing = true
      );
      return cleanup;
    }
  }, [
    refreshFlag,
    activeTab,
    jobData.id,
    fetchLogs,
    setIsLoadingControllerLogs,
    safeAbort,
  ]);

  // Comprehensive cleanup when component unmounts or user navigates away
  useEffect(() => {
    return () => {
      console.log('Cleaning up managed job log requests...');

      // Abort all active log requests
      if (activeLogsRequest.current) {
        safeAbort(activeLogsRequest.current, 'logs cleanup');
        activeLogsRequest.current = null;
      }

      if (activeControllerLogsRequest.current) {
        safeAbort(
          activeControllerLogsRequest.current,
          'controller logs cleanup'
        );
        activeControllerLogsRequest.current = null;
      }

      // Clear all timeouts
      if (updateTimeoutRef.current) {
        clearTimeout(updateTimeoutRef.current);
        updateTimeoutRef.current = null;
      }

      // Clear any pending batches
      logsBatchRef.current = '';
      controllerLogsBatchRef.current = '';

      // Reset loading states to prevent any UI inconsistencies
      setIsLoadingLogs(false);
      setIsLoadingControllerLogs(false);
      setIsRefreshingLogs(false);
      setIsRefreshingControllerLogs(false);
    };
  }, [safeAbort, setIsLoadingLogs, setIsLoadingControllerLogs]); // Empty dependency array means this runs only on unmount

  // Handle page visibility changes to pause streaming when tab is not active
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.hidden) {
        console.log('Page hidden - pausing log streaming for performance');

        // Optionally abort active requests when page is hidden
        // Uncomment if you want to completely stop streaming when tab is not visible
        /*
        if (activeLogsRequest.current) {
          safeAbort(activeLogsRequest.current, 'logs visibility');
          activeLogsRequest.current = null;
        }
        if (activeControllerLogsRequest.current) {
          safeAbort(activeControllerLogsRequest.current, 'controller logs visibility');
          activeControllerLogsRequest.current = null;
        }
        */

        // Clear any pending batch updates to reduce background processing
        if (updateTimeoutRef.current) {
          clearTimeout(updateTimeoutRef.current);
          updateTimeoutRef.current = null;
        }
      } else {
        console.log('Page visible - resuming normal operation');
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);

  // Auto-scroll to bottom when logs change or tab changes
  useEffect(() => {
    const performScroll = () => {
      if (
        (activeTab === 'logs' && logs) ||
        (activeTab === 'controllerlogs' && controllerLogs)
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
        ) : hasReceivedFirstChunk || logs ? (
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
        ) : hasReceivedFirstChunk || controllerLogs ? (
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
        <div className="text-gray-600 font-medium text-base">Worker Pool</div>
        <div className="text-base mt-1">{jobData.pool || '-'}</div>
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

export default JobDetails;
