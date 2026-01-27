import React, { useState, useEffect } from 'react';
import { CircularProgress } from '@mui/material';
import { useRouter } from 'next/router';
import { Card } from '@/components/ui/card';
import { useSingleManagedJob, getPoolStatus } from '@/data/connectors/jobs';
import Link from 'next/link';
import {
  RotateCwIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  Download,
  ExternalLinkIcon,
} from 'lucide-react';
import {
  CustomTooltip as Tooltip,
  formatFullTimestamp,
  formatDuration,
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
import { UserDisplay } from '@/components/elements/UserDisplay';
import dashboardCache from '@/lib/cache';
import { useLogStreamer } from '@/hooks/useLogStreamer';
import {
  checkGrafanaAvailability,
  getGrafanaUrl,
  buildGrafanaUrl,
} from '@/utils/grafana';

function TaskDetails() {
  const router = useRouter();
  const { job: jobId, task: taskIndex } = router.query;
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const { jobData, loading } = useSingleManagedJob(jobId, refreshTrigger);
  const [poolsData, setPoolsData] = useState([]);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [refreshLogsFlag, setRefreshLogsFlag] = useState(0);
  const [isLogsExpanded, setIsLogsExpanded] = useState(true);
  const isMobile = useMobile();

  // GPU metrics state
  const [isGrafanaAvailable, setIsGrafanaAvailable] = useState(false);
  const [timeRange, setTimeRange] = useState({ from: 'now-1h', to: 'now' });
  const [gpuMetricsRefreshTrigger, setGpuMetricsRefreshTrigger] = useState(0);
  const GPU_METRICS_EXPANDED_KEY = 'skypilot-task-gpu-metrics-expanded';
  const [isGpuMetricsExpanded, setIsGpuMetricsExpanded] = useState(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem(GPU_METRICS_EXPANDED_KEY);
      return saved === 'true';
    }
    return false;
  });

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

  // Check Grafana availability on mount
  useEffect(() => {
    const checkGrafana = async () => {
      const available = await checkGrafanaAvailability();
      setIsGrafanaAvailable(available);
    };

    if (typeof window !== 'undefined') {
      checkGrafana();
    }
  }, []);

  // Handle manual refresh
  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    try {
      setRefreshTrigger((prev) => prev + 1);
      setRefreshLogsFlag((prev) => prev + 1);
      setGpuMetricsRefreshTrigger((prev) => prev + 1);
    } catch (error) {
      console.error('Error refreshing data:', error);
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleLogsRefresh = () => {
    setRefreshLogsFlag((prev) => prev + 1);
  };

  // GPU metrics helper functions
  const handleTimeRangePreset = (preset) => {
    setTimeRange({
      from: `now-${preset}`,
      to: 'now',
    });
  };

  const toggleGpuMetricsExpanded = () => {
    const newValue = !isGpuMetricsExpanded;
    setIsGpuMetricsExpanded(newValue);
    if (typeof window !== 'undefined') {
      localStorage.setItem(GPU_METRICS_EXPANDED_KEY, String(newValue));
    }
  };

  // Build Grafana panel URL with filters
  const buildGrafanaMetricsUrl = (panelId, clusterNameOnCloud) => {
    const grafanaUrl = getGrafanaUrl();
    return `${grafanaUrl}/d-solo/skypilot-dcgm-gpu/skypilot-dcgm-gpu-metrics?orgId=1&from=${encodeURIComponent(timeRange.from)}&to=${encodeURIComponent(timeRange.to)}&timezone=browser&var-cluster=${encodeURIComponent(clusterNameOnCloud)}&var-node=$__all&var-gpu=$__all&theme=light&panelId=${panelId}&__feature.dashboardSceneSolo`;
  };

  // GPU panels configuration
  const gpuPanels = [
    { id: '1', title: 'GPU Utilization', keyPrefix: 'gpu-util' },
    { id: '2', title: 'GPU Memory Utilization', keyPrefix: 'gpu-memory' },
    { id: '3', title: 'GPU Temperature', keyPrefix: 'gpu-temp' },
    { id: '4', title: 'GPU Power Usage', keyPrefix: 'gpu-power' },
  ];

  if (!router.isReady) {
    return <div>Loading...</div>;
  }

  // Get all tasks for this job
  const allTasks =
    jobData?.jobs?.filter((item) => String(item.id) === String(jobId)) || [];

  // Get the specific task by index
  const taskIndexNum = parseInt(taskIndex, 10);
  const taskData = allTasks[taskIndexNum] || null;
  const jobName = allTasks.length > 0 ? allTasks[0].name : '';

  const title = taskData
    ? `Task ${taskIndex}: ${taskData.task || 'Unnamed'} | Job ${jobId} | SkyPilot Dashboard`
    : 'Task Details | SkyPilot Dashboard';

  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <>
        <div className="flex items-center justify-between mb-4">
          <div className="text-base flex items-center flex-wrap">
            <Link href="/jobs" className="text-sky-blue hover:underline">
              Managed Jobs
            </Link>
            <span className="mx-2 text-gray-500">›</span>
            <Link
              href={`/jobs/${jobId}`}
              className="text-sky-blue hover:underline"
            >
              {jobId} {jobName ? `(${jobName})` : ''}
            </Link>
            <span className="mx-2 text-gray-500">›</span>
            <span className="text-gray-700">
              Task {taskIndex}
              {taskData?.task && (
                <span className="text-gray-500"> ({taskData.task})</span>
              )}
            </span>
          </div>

          <div className="text-sm flex items-center">
            {(loading || isRefreshing || isLoadingLogs) && (
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
        ) : taskData ? (
          <div className="space-y-8">
            {/* Task Details Section */}
            <div id="details-section">
              <Card>
                <div className="flex items-center justify-between px-4 pt-4">
                  <h3 className="text-lg font-semibold">Task Details</h3>
                </div>
                <div className="p-4">
                  <TaskDetailsContent
                    taskData={taskData}
                    taskIndex={taskIndexNum}
                    poolsData={poolsData}
                  />
                </div>
              </Card>
            </div>

            {/* GPU Metrics Section - Show for Kubernetes tasks with cluster_name_on_cloud */}
            {isGrafanaAvailable &&
              taskData.full_infra?.includes('Kubernetes') &&
              !taskData.pool &&
              taskData.cluster_name_on_cloud && (
                <div className="mt-6">
                  <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
                    <div
                      className={`flex items-center justify-between px-4 ${isGpuMetricsExpanded ? 'pt-4' : 'py-4'}`}
                    >
                      <button
                        onClick={toggleGpuMetricsExpanded}
                        className="flex items-center text-left focus:outline-none hover:text-gray-700 transition-colors duration-200"
                      >
                        {isGpuMetricsExpanded ? (
                          <ChevronDownIcon className="w-5 h-5 mr-2" />
                        ) : (
                          <ChevronRightIcon className="w-5 h-5 mr-2" />
                        )}
                        <h3 className="text-lg font-semibold">GPU Metrics</h3>
                      </button>
                      <Tooltip content="Open in Grafana">
                        <button
                          onClick={() => {
                            const dashboardPath =
                              '/d/skypilot-dcgm-gpu/skypilot-dcgm-gpu-metrics';
                            const queryParams = new URLSearchParams({
                              orgId: '1',
                              from: timeRange.from,
                              to: timeRange.to,
                              timezone: 'browser',
                              'var-cluster': taskData.cluster_name_on_cloud,
                              'var-node': '$__all',
                              'var-gpu': '$__all',
                            });
                            window.open(
                              buildGrafanaUrl(
                                `${dashboardPath}?${queryParams.toString()}`
                              ),
                              '_blank'
                            );
                          }}
                          className="p-1.5 rounded-md text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
                          aria-label="Open in Grafana"
                        >
                          <ExternalLinkIcon className="w-4 h-4" />
                        </button>
                      </Tooltip>
                    </div>
                    {isGpuMetricsExpanded && (
                      <div className="p-5">
                        {/* Filtering Controls */}
                        <div className="mb-4 p-4 bg-gray-50 rounded-md border border-gray-200">
                          <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center">
                            {/* Time Range Selection */}
                            <div className="flex items-center gap-2">
                              <label className="text-sm font-medium text-gray-700 whitespace-nowrap">
                                Time Range:
                              </label>
                              <div className="flex gap-1">
                                {[
                                  { label: '15m', value: '15m' },
                                  { label: '1h', value: '1h' },
                                  { label: '6h', value: '6h' },
                                  { label: '24h', value: '24h' },
                                  { label: '7d', value: '7d' },
                                ].map((preset) => (
                                  <button
                                    key={preset.value}
                                    onClick={() =>
                                      handleTimeRangePreset(preset.value)
                                    }
                                    className={`px-2 py-1 text-xs font-medium rounded border transition-colors ${
                                      timeRange.from ===
                                        `now-${preset.value}` &&
                                      timeRange.to === 'now'
                                        ? 'bg-sky-blue text-white border-sky-blue'
                                        : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                                    }`}
                                  >
                                    {preset.label}
                                  </button>
                                ))}
                              </div>
                            </div>
                          </div>

                          {/* Show current selection info */}
                          <div className="mt-2 text-xs text-gray-500">
                            Showing: {taskData.task || `Task ${taskIndex}`} •
                            Time: {timeRange.from} to {timeRange.to}
                          </div>
                        </div>

                        <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(300px,1fr))]">
                          {gpuPanels.map((panel) => (
                            <div
                              key={panel.id}
                              className="bg-white rounded-md border border-gray-200 shadow-sm"
                            >
                              <div className="p-2">
                                <iframe
                                  src={buildGrafanaMetricsUrl(
                                    panel.id,
                                    taskData.cluster_name_on_cloud
                                  )}
                                  width="100%"
                                  height="400"
                                  frameBorder="0"
                                  title={panel.title}
                                  className="rounded"
                                  key={`${panel.keyPrefix}-${taskData.cluster_name_on_cloud}-${timeRange.from}-${timeRange.to}-${gpuMetricsRefreshTrigger}`}
                                />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

            {/* Logs Section */}
            <div id="logs-section" className="mt-6">
              <Card>
                <button
                  onClick={() => setIsLogsExpanded(!isLogsExpanded)}
                  className="flex items-center justify-between w-full px-4 py-4 text-left focus:outline-none"
                >
                  <div className="flex items-center">
                    {isLogsExpanded ? (
                      <ChevronDownIcon className="w-5 h-5 mr-2 text-gray-500" />
                    ) : (
                      <ChevronRightIcon className="w-5 h-5 mr-2 text-gray-500" />
                    )}
                    <h3 className="text-lg font-semibold">Logs</h3>
                    <span className="ml-2 text-xs text-gray-500">
                      (Task {taskIndex} logs)
                    </span>
                  </div>
                  {isLogsExpanded && (
                    <div className="flex items-center space-x-3">
                      <Tooltip
                        content="Download task logs"
                        className="text-muted-foreground"
                      >
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            downloadManagedJobLogs({
                              jobId: parseInt(jobId),
                              controller: false,
                            });
                          }}
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
                          onClick={(e) => {
                            e.stopPropagation();
                            handleLogsRefresh();
                          }}
                          disabled={isLoadingLogs}
                          className="text-sky-blue hover:text-sky-blue-bright flex items-center"
                        >
                          <RotateCwIcon
                            className={`w-4 h-4 ${isLoadingLogs ? 'animate-spin' : ''}`}
                          />
                        </button>
                      </Tooltip>
                    </div>
                  )}
                </button>
                {isLogsExpanded && (
                  <div className="p-4">
                    <TaskLogsContent
                      taskData={taskData}
                      taskIndex={taskIndexNum}
                      refreshFlag={refreshLogsFlag}
                      setIsLoadingLogs={setIsLoadingLogs}
                      isLoadingLogs={isLoadingLogs}
                    />
                  </div>
                )}
              </Card>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center py-32">
            <span>Task not found</span>
          </div>
        )}
      </>
    </>
  );
}

function TaskDetailsContent({ taskData, taskIndex, poolsData }) {
  return (
    <div className="grid grid-cols-2 gap-6">
      <div>
        <div className="text-gray-600 font-medium text-base">Task</div>
        <div className="text-base mt-1">
          {taskIndex}
          {taskData.task && (
            <span className="text-gray-500"> ({taskData.task})</span>
          )}
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Job</div>
        <div className="text-base mt-1">
          <Link
            href={`/jobs/${taskData.id}`}
            className="text-sky-blue hover:text-sky-blue-bright hover:underline"
          >
            {taskData.id}
            {taskData.name ? ` (${taskData.name})` : ''}
          </Link>
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Status</div>
        <div className="text-base mt-1">
          <StatusBadge status={taskData.status} />
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">User</div>
        <div className="text-base mt-1">
          <UserDisplay username={taskData.user} userHash={taskData.user_hash} />
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Workspace</div>
        <div className="text-base mt-1">
          <Link
            href="/workspaces"
            className="text-gray-700 hover:text-blue-600 hover:underline"
          >
            {taskData.workspace || 'default'}
          </Link>
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Duration</div>
        <div className="text-base mt-1">
          {formatDuration(taskData.job_duration)}
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">
          Requested Resources
        </div>
        <div className="text-base mt-1">
          {taskData.requested_resources || taskData.resources_str || 'N/A'}
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Infra</div>
        <div className="text-base mt-1">
          {taskData.infra ? (
            <NonCapitalizedTooltip
              content={taskData.full_infra || taskData.infra}
              className="text-sm text-muted-foreground"
            >
              <span>
                <Link href="/infra" className="text-blue-600 hover:underline">
                  {taskData.cloud || taskData.infra.split('(')[0].trim()}
                </Link>
                {taskData.infra.includes('(') && (
                  <span>
                    {' ' +
                      taskData.infra.substring(taskData.infra.indexOf('('))}
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
        <div className="text-gray-600 font-medium text-base">Recoveries</div>
        <div className="text-base mt-1">{taskData.recoveries || 0}</div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Pool</div>
        <div className="text-base mt-1">
          {renderPoolLink(taskData.pool, taskData.pool_hash, poolsData)}
        </div>
      </div>
      {taskData.details && (
        <div className="col-span-2">
          <div className="text-gray-600 font-medium text-base">Details</div>
          <div className="text-base mt-1 text-gray-700 whitespace-pre-wrap">
            {taskData.details}
          </div>
        </div>
      )}
    </div>
  );
}

function TaskLogsContent({
  taskData,
  taskIndex,
  refreshFlag,
  setIsLoadingLogs,
  isLoadingLogs,
}) {
  const PENDING_STATUSES = ['PENDING', 'SUBMITTED', 'STARTING'];
  const RECOVERING_STATUSES = ['RECOVERING'];

  const isPending = PENDING_STATUSES.includes(taskData.status);
  const isRecovering = RECOVERING_STATUSES.includes(taskData.status);

  const logStreamArgs = React.useMemo(
    () => ({
      jobId: taskData.id,
      task: taskIndex,
      controller: false,
    }),
    [taskData.id, taskIndex]
  );

  const handleLogsError = React.useCallback((error) => {
    console.error('Error streaming logs:', error);
  }, []);

  const {
    lines: logs,
    isLoading: streamingLogsLoading,
    hasReceivedFirstChunk: hasReceivedLogChunk,
  } = useLogStreamer({
    streamFn: streamManagedJobLogs,
    streamArgs: logStreamArgs,
    enabled: !isPending && !isRecovering,
    refreshTrigger: refreshFlag,
    onError: handleLogsError,
  });

  React.useEffect(() => {
    setIsLoadingLogs(streamingLogsLoading);
  }, [streamingLogsLoading, setIsLoadingLogs]);

  return (
    <div className="max-h-96 overflow-y-auto">
      {isPending ? (
        <div className="bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500">
          <span>Waiting for the task to start; refresh in a few moments.</span>
        </div>
      ) : isRecovering ? (
        <div className="bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500">
          <span>
            Waiting for the task to recover; refresh in a few moments.
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

export default TaskDetails;
