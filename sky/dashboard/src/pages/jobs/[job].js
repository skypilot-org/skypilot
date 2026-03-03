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
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  useSingleManagedJob,
  getPoolStatus,
  computeJobGroupStatus,
} from '@/data/connectors/jobs';
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
  formatDuration,
  renderPoolLink,
  extractNodeTypes,
} from '@/components/utils';
import { LogFilter } from '@/components/utils';
import {
  streamManagedJobLogs,
  downloadManagedJobLogs,
} from '@/data/connectors/jobs';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { PrimaryBadge } from '@/components/elements/PrimaryBadge';
import { useMobile } from '@/hooks/useMobile';
import Head from 'next/head';
import { NonCapitalizedTooltip } from '@/components/utils';
import { formatJobYaml } from '@/lib/yamlUtils';
import { UserDisplay } from '@/components/elements/UserDisplay';
import { YamlHighlighter } from '@/components/YamlHighlighter';
import dashboardCache from '@/lib/cache';
import { PluginSlot } from '@/plugins/PluginSlot';
import { checkGrafanaAvailability } from '@/utils/grafana';
import { GPUMetricsSection } from '@/components/GPUMetricsSection';
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
  const [selectedTaskIndex, setSelectedTaskIndex] = useState(0);
  const [selectedNode, setSelectedNode] = useState('all');
  const [logNodes, setLogNodes] = useState([]);
  const [logExtractedLinks, setLogExtractedLinks] = useState({});
  const isMobile = useMobile();

  // GPU metrics state
  const [isGrafanaAvailable, setIsGrafanaAvailable] = useState(false);
  // GPU metrics task selection for job groups
  const [gpuMetricsTaskIndex, setGpuMetricsTaskIndex] = useState(0);
  const GPU_METRICS_EXPANDED_KEY = 'skypilot-jobs-gpu-metrics-expanded';

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
    checkGrafana();
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
      // Trigger GPU metrics refresh
      setGpuMetricsRefreshTrigger((prev) => prev + 1);
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

  // Get all tasks for this job (supports multi-task jobs) - computed early for GPU metrics
  const allTasksForGpuMetrics = useMemo(() => {
    return (
      jobData?.jobs?.filter((item) => String(item.id) === String(jobId)) || []
    );
  }, [jobData, jobId]);

  // Determine which tasks have GPU metrics (Kubernetes, not pool, has cluster_name_on_cloud)
  const tasksWithGpuMetrics = useMemo(() => {
    return allTasksForGpuMetrics.map((task, index) => ({
      index,
      task,
      hasMetrics:
        task.full_infra?.includes('Kubernetes') &&
        !task.pool &&
        task.cluster_name_on_cloud,
    }));
  }, [allTasksForGpuMetrics]);

  const hasAnyTaskWithGpuMetrics = tasksWithGpuMetrics.some(
    (t) => t.hasMetrics
  );

  // Get the currently selected task for GPU metrics
  const gpuMetricsTask =
    allTasksForGpuMetrics[gpuMetricsTaskIndex] || allTasksForGpuMetrics[0];

  // Get cluster name for GPU metrics from selected task
  const gpuMetricsClusterName =
    gpuMetricsTask?.cluster_name_on_cloud ||
    allTasksForGpuMetrics[0]?.cluster_name_on_cloud;

  if (!router.isReady) {
    return <div>Loading...</div>;
  }

  // Get all tasks for this job (supports multi-task jobs)
  const allTasks =
    jobData?.jobs?.filter((item) => String(item.id) === String(jobId)) || [];

  // Use the first task for main details display
  const detailJobData = allTasks.length > 0 ? allTasks[0] : null;
  const isMultiTask = allTasks.length > 1;

  // For multi-task jobs, find fields from any task (they may only be on one task)
  const jobYaml =
    allTasks.find((t) => t.dag_yaml)?.dag_yaml || detailJobData?.dag_yaml;
  const jobEntrypoint =
    allTasks.find((t) => t.entrypoint)?.entrypoint || detailJobData?.entrypoint;
  const jobIsJobGroup =
    allTasks.find((t) => t.is_job_group)?.is_job_group ||
    detailJobData?.is_job_group ||
    allTasks.length > 1;

  // For execution, check stored values first, then apply defaults for multi-task jobs
  // Older jobs may not have these fields stored, so provide sensible defaults
  const storedExecution =
    allTasks.find((t) => t.execution)?.execution || detailJobData?.execution;
  // Default execution to 'parallel' for multi-task jobs without stored value
  const jobExecution = storedExecution || (isMultiTask ? 'parallel' : null);

  // Enhanced job data with fields from any task
  const enhancedJobData = detailJobData
    ? {
        ...detailJobData,
        dag_yaml: jobYaml,
        entrypoint: jobEntrypoint,
        execution: jobExecution,
        is_job_group: jobIsJobGroup,
      }
    : null;

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
              {isMultiTask && (
                <span className="ml-2 text-xs text-gray-500 bg-gray-200 px-1.5 py-0.5 rounded">
                  {allTasks.length} tasks
                </span>
              )}
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
                    jobData={enhancedJobData}
                    allTasks={allTasks}
                    activeTab="info"
                    setIsLoadingLogs={setIsLoadingLogs}
                    setIsLoadingControllerLogs={setIsLoadingControllerLogs}
                    isLoadingLogs={isLoadingLogs}
                    isLoadingControllerLogs={isLoadingControllerLogs}
                    refreshFlag={0}
                    poolsData={poolsData}
                    links={enhancedJobData?.links}
                    logExtractedLinks={logExtractedLinks}
                    onLinksExtracted={setLogExtractedLinks}
                  />
                </div>
              </Card>
            </div>

            {/* Tasks Section - only show for multi-task jobs */}
            {isMultiTask && (
              <div id="tasks-section" className="mt-6">
                <Card>
                  <div className="flex items-center justify-between px-4 pt-4">
                    <h3 className="text-lg font-semibold flex items-center">
                      Tasks
                      <span className="ml-2 text-sm font-normal text-gray-500">
                        ({allTasks.length} tasks)
                      </span>
                    </h3>
                  </div>
                  <div className="p-4">
                    <div className="overflow-x-auto rounded-lg border">
                      <Table className="min-w-full">
                        <TableHeader>
                          <TableRow>
                            <TableHead className="whitespace-nowrap">
                              ID
                            </TableHead>
                            <TableHead className="whitespace-nowrap">
                              Name
                            </TableHead>
                            <TableHead className="whitespace-nowrap">
                              Status
                            </TableHead>
                            <TableHead className="whitespace-nowrap">
                              Duration
                            </TableHead>
                            <TableHead className="whitespace-nowrap">
                              Infra
                            </TableHead>
                            <TableHead className="whitespace-nowrap">
                              Resources
                            </TableHead>
                            <TableHead className="whitespace-nowrap">
                              Recoveries
                            </TableHead>
                            <TableHead className="whitespace-nowrap">
                              Logs
                            </TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {allTasks.map((task, index) => (
                            <TableRow
                              key={task.task_job_id}
                              className="hover:bg-gray-50"
                            >
                              <TableCell>
                                <Link
                                  href={`/jobs/${jobId}/${index}`}
                                  className="text-blue-600 hover:underline"
                                >
                                  {index}
                                </Link>
                              </TableCell>
                              <TableCell>
                                <Link
                                  href={`/jobs/${jobId}/${index}`}
                                  className="text-blue-600 hover:underline"
                                >
                                  {task.task || `Job ${index}`}
                                  {/* Show Primary badge for primary tasks in job groups with auxiliaries */}
                                  {allTasks.some(
                                    (t) => t.is_primary_in_job_group === false
                                  ) &&
                                    task.is_primary_in_job_group === true && (
                                      <span className="ml-1.5">
                                        <PrimaryBadge />
                                      </span>
                                    )}
                                </Link>
                              </TableCell>
                              <TableCell>
                                <StatusBadge status={task.status} />
                              </TableCell>
                              <TableCell>
                                {formatDuration(task.job_duration)}
                              </TableCell>
                              <TableCell>
                                {task.infra && task.infra !== '-' ? (
                                  <NonCapitalizedTooltip
                                    content={task.full_infra || task.infra}
                                    className="text-sm text-muted-foreground"
                                  >
                                    <span>
                                      {task.cloud ||
                                        task.infra.split('(')[0].trim()}
                                      {task.infra.includes('(') && (
                                        <span className="text-gray-500">
                                          {' ' +
                                            task.infra.substring(
                                              task.infra.indexOf('(')
                                            )}
                                        </span>
                                      )}
                                    </span>
                                  </NonCapitalizedTooltip>
                                ) : (
                                  <span>-</span>
                                )}
                              </TableCell>
                              <TableCell>
                                <NonCapitalizedTooltip
                                  content={
                                    task.requested_resources ||
                                    task.resources_str_full ||
                                    task.resources_str ||
                                    '-'
                                  }
                                  className="text-sm text-muted-foreground"
                                >
                                  <span>
                                    {task.requested_resources ||
                                      task.resources_str ||
                                      '-'}
                                  </span>
                                </NonCapitalizedTooltip>
                              </TableCell>
                              <TableCell>{task.recoveries || 0}</TableCell>
                              <TableCell>
                                <Tooltip
                                  content="Download job logs"
                                  className="text-muted-foreground"
                                >
                                  <button
                                    onClick={() =>
                                      downloadManagedJobLogs({
                                        jobId: parseInt(jobId),
                                        controller: false,
                                      })
                                    }
                                    className="text-sky-blue hover:text-sky-blue-bright"
                                  >
                                    <Download className="w-4 h-4" />
                                  </button>
                                </Tooltip>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                </Card>
              </div>
            )}

            {/* GPU Metrics Section - Show for Kubernetes managed jobs with cluster_name_on_cloud */}
            {isGrafanaAvailable && hasAnyTaskWithGpuMetrics && (
              <GPUMetricsSection
                clusterNameOnCloud={gpuMetricsClusterName}
                displayName={
                  isMultiTask
                    ? `${gpuMetricsTask?.task || gpuMetricsTask?.name || detailJobData.name} (Task ${gpuMetricsTaskIndex})`
                    : gpuMetricsTask?.task ||
                      gpuMetricsTask?.name ||
                      detailJobData.name
                }
                storageKey={GPU_METRICS_EXPANDED_KEY}
                noMetricsMessage={
                  gpuMetricsTask?.pool
                    ? 'GPU metrics are not available for pool jobs.'
                    : !gpuMetricsTask?.full_infra?.includes('Kubernetes')
                      ? 'GPU metrics are only available for Kubernetes tasks.'
                      : 'No GPU metrics available for this task.'
                }
                headerExtra={
                  isMultiTask && (
                    <Select
                      onValueChange={(value) =>
                        setGpuMetricsTaskIndex(parseInt(value, 10))
                      }
                      value={String(gpuMetricsTaskIndex)}
                    >
                      <SelectTrigger
                        onClick={(e) => e.stopPropagation()}
                        aria-label="Task"
                        className="focus:ring-0 focus:ring-offset-0 h-8 w-auto min-w-[160px] text-sm ml-4"
                      >
                        <SelectValue placeholder="Select Task" />
                      </SelectTrigger>
                      <SelectContent>
                        {tasksWithGpuMetrics.map(
                          ({ index, task, hasMetrics }) => (
                            <SelectItem
                              key={index}
                              value={String(index)}
                              disabled={!hasMetrics}
                            >
                              Task {index}
                              {task.task ? `: ${task.task}` : ''}
                              {!hasMetrics ? ' (no metrics)' : ''}
                            </SelectItem>
                          )
                        )}
                      </SelectContent>
                    </Select>
                  )
                }
              />
            )}

            {/* Plugin Slot: Job Infra Nodes */}
            <PluginSlot
              name="jobs.detail.nodes"
              context={{
                clusterName: detailJobData.current_cluster_name,
                clusterNameOnCloud: detailJobData.cluster_name_on_cloud,
                nodeNames: detailJobData.node_names,
                infra: detailJobData.full_infra,
                status: detailJobData.status,
              }}
              wrapperClassName="mt-6"
            />

            {/* Logs Section */}
            <div id="logs-section" className="mt-6">
              <Card>
                <div className="flex items-center justify-between px-4 pt-4">
                  <div className="flex items-center gap-4">
                    <h3 className="text-lg font-semibold">Logs</h3>
                    {isMultiTask && (
                      <Select
                        onValueChange={(value) =>
                          setSelectedTaskIndex(parseInt(value, 10))
                        }
                        value={String(selectedTaskIndex)}
                      >
                        <SelectTrigger
                          aria-label="Task"
                          className="focus:ring-0 focus:ring-offset-0 h-8 w-auto min-w-[160px] text-sm"
                        >
                          <SelectValue placeholder="Select Task" />
                        </SelectTrigger>
                        <SelectContent>
                          {allTasks.map((task, index) => (
                            <SelectItem
                              key={task.task_job_id || index}
                              value={String(index)}
                            >
                              Task {index}
                              {task.task ? `: ${task.task}` : ''}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                    <Select
                      onValueChange={(value) => setSelectedNode(value)}
                      value={selectedNode}
                    >
                      <SelectTrigger
                        aria-label="Node"
                        className="focus:ring-0 focus:ring-offset-0 h-8 w-auto min-w-[120px] text-sm"
                      >
                        <SelectValue placeholder="All Nodes" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Nodes</SelectItem>
                        {logNodes.map((node) => (
                          <SelectItem key={node} value={node}>
                            {node}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <span className="text-xs text-gray-500">
                      (Logs are not streaming; click refresh to fetch the latest
                      logs.)
                    </span>
                  </div>
                  <div className="flex items-center space-x-3">
                    <Tooltip
                      content="Download all job logs (zip)"
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
                    jobData={
                      isMultiTask ? allTasks[selectedTaskIndex] : detailJobData
                    }
                    allTasks={allTasks}
                    activeTab="logs"
                    setIsLoadingLogs={setIsLoadingLogs}
                    setIsLoadingControllerLogs={setIsLoadingControllerLogs}
                    isLoadingLogs={isLoadingLogs}
                    isLoadingControllerLogs={isLoadingControllerLogs}
                    refreshFlag={refreshLogsFlag}
                    poolsData={poolsData}
                    selectedTaskIndex={isMultiTask ? selectedTaskIndex : null}
                    selectedNode={selectedNode}
                    onNodesExtracted={setLogNodes}
                    onLinksExtracted={setLogExtractedLinks}
                  />
                </div>
              </Card>
            </div>

            {/* Plugin Slot: Job Detail Events */}
            <PluginSlot
              name="jobs.detail.events"
              context={{
                jobId: detailJobData.id,
              }}
              wrapperClassName="mt-6"
            />

            {/* Controller Logs Section - Collapsible */}
            <ControllerLogsSection
              jobId={jobId}
              detailJobData={detailJobData}
              isLoadingControllerLogs={isLoadingControllerLogs}
              handleControllerLogsRefresh={handleControllerLogsRefresh}
              setIsLoadingControllerLogs={setIsLoadingControllerLogs}
              setIsLoadingLogs={setIsLoadingLogs}
              refreshControllerLogsFlag={refreshControllerLogsFlag}
              poolsData={poolsData}
            />
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

function ControllerLogsSection({
  jobId,
  detailJobData,
  isLoadingControllerLogs,
  handleControllerLogsRefresh,
  setIsLoadingControllerLogs,
  setIsLoadingLogs,
  refreshControllerLogsFlag,
  poolsData,
}) {
  const CONTROLLER_LOGS_EXPANDED_KEY = 'skypilot-controller-logs-expanded';

  // Initialize state from localStorage
  const [isExpanded, setIsExpanded] = useState(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem(CONTROLLER_LOGS_EXPANDED_KEY);
      return saved === 'true';
    }
    return false;
  });

  // Persist state to localStorage when it changes
  const toggleExpanded = () => {
    const newValue = !isExpanded;
    setIsExpanded(newValue);
    if (typeof window !== 'undefined') {
      localStorage.setItem(CONTROLLER_LOGS_EXPANDED_KEY, String(newValue));
    }
  };

  return (
    <div id="controller-logs-section" className="mt-6">
      <Card>
        <div
          className={`flex items-center justify-between px-4 ${isExpanded ? 'pt-4' : 'py-4'}`}
        >
          <button
            onClick={toggleExpanded}
            className="flex items-center text-left focus:outline-none hover:text-gray-700 transition-colors duration-200"
          >
            {isExpanded ? (
              <ChevronDownIcon className="w-5 h-5 mr-2" />
            ) : (
              <ChevronRightIcon className="w-5 h-5 mr-2" />
            )}
            <h3 className="text-lg font-semibold">Controller Logs</h3>
            <span className="ml-2 text-xs text-gray-500">
              (Logs are not streaming; click refresh to fetch the latest logs.)
            </span>
          </button>
          {isExpanded && (
            <div className="flex items-center space-x-3">
              <Tooltip
                content="Download full controller logs"
                className="text-muted-foreground"
              >
                <button
                  onClick={() =>
                    downloadManagedJobLogs({
                      jobId: parseInt(Array.isArray(jobId) ? jobId[0] : jobId),
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
          )}
        </div>
        {isExpanded && (
          <div className="p-4">
            <JobDetailsContent
              jobData={detailJobData}
              activeTab="controllerlogs"
              setIsLoadingLogs={setIsLoadingLogs}
              setIsLoadingControllerLogs={setIsLoadingControllerLogs}
              isLoadingLogs={false}
              isLoadingControllerLogs={isLoadingControllerLogs}
              refreshFlag={refreshControllerLogsFlag}
              poolsData={poolsData}
            />
          </div>
        )}
      </Card>
    </div>
  );
}
// URL patterns for extracting links from logs
// Each pattern has a name (used as link label) and a regex to match entire tokens
// Patterns use ^ and $ anchors for exact token matching
const URL_PATTERNS = {
  'W&B Run': /^https:\/\/wandb\.ai\/[^\/]+\/[^\/]+\/runs\/[^\/]+$/,
};

function JobDetailsContent({
  jobData,
  allTasks = [],
  activeTab,
  setIsLoadingLogs,
  setIsLoadingControllerLogs,
  isLoadingLogs,
  isLoadingControllerLogs,
  refreshFlag,
  poolsData,
  links,
  logExtractedLinks: logExtractedLinksFromParent,
  onLinksExtracted,
  selectedTaskIndex = null,
  onTaskChange = null,
  selectedNode = 'all',
  onNodesExtracted = null,
}) {
  const [isYamlExpanded, setIsYamlExpanded] = useState(false);
  const [expandedYamlDocs, setExpandedYamlDocs] = useState({});
  const [showFullYaml, setShowFullYaml] = useState(false);
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

  // Compute job group status based on primary tasks
  // For job groups with primary/auxiliary tasks, status is determined only by primary tasks
  const computedStatus = useMemo(() => {
    if (allTasks.length > 1) {
      return computeJobGroupStatus(allTasks);
    }
    return jobData.status;
  }, [allTasks, jobData.status]);

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
      // Build JobGroup header with name and execution
      const hasJobGroupConfig = jobData.name || jobData.execution;
      const jobGroupHeader = hasJobGroupConfig
        ? [
            jobData.name ? `name: ${jobData.name}` : null,
            jobData.execution ? `execution: ${jobData.execution}` : null,
          ]
            .filter(Boolean)
            .join('\n') + '\n---\n'
        : '';

      let textToCopy = '';

      if (yamlDocs.length === 1) {
        // Single document - use the formatted content directly
        textToCopy = jobGroupHeader + yamlDocs[0].content;
      } else if (yamlDocs.length > 1) {
        // Multiple documents - join them with document separators
        textToCopy =
          jobGroupHeader + yamlDocs.map((doc) => doc.content).join('\n---\n');
      } else {
        // Fallback to raw YAML if formatting fails
        textToCopy = jobGroupHeader + jobData.dag_yaml;
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
      // Pass task index (as int) when viewing a specific task in a multi-task job
      task: selectedTaskIndex,
    }),
    [jobData.id, selectedTaskIndex]
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

  // Extract node types from logs and pass them to parent
  useEffect(() => {
    if (onNodesExtracted && logs.length > 0) {
      const logsText = logs.join('\n');
      const nodes = extractNodeTypes(logsText);
      onNodesExtracted(nodes);
    }
  }, [logs, onNodesExtracted]);

  // Persist extracted links across tab changes using a ref
  const extractedLinksRef = useRef({});

  // Extract URLs from logs using whitelisted patterns
  // Processes line-by-line with tokenization for exact word-level matching
  // Updates are accumulated in a ref so they persist when switching tabs
  const logExtractedLinks = useMemo(() => {
    // Start with previously found links
    const extractedLinks = { ...extractedLinksRef.current };
    const foundPatterns = new Set(Object.keys(extractedLinks));

    // Process line by line to avoid creating one large string
    for (const line of logs) {
      // Skip if we've found all patterns
      if (foundPatterns.size === Object.keys(URL_PATTERNS).length) {
        break;
      }

      // Split line into tokens by whitespace and common delimiters
      // This handles cases like: "URL: https://..." or "(https://...)"
      const tokens = line.split(/[\s"'<>()[\]{},;]+/);

      for (const token of tokens) {
        // Clean up trailing punctuation that might be attached
        const cleanToken = token.replace(/[.,:;!?]+$/, '');
        if (!cleanToken) continue;

        // Check each pattern against the clean token
        for (const [linkName, pattern] of Object.entries(URL_PATTERNS)) {
          if (foundPatterns.has(linkName)) continue;

          if (pattern.test(cleanToken)) {
            extractedLinks[linkName] = cleanToken;
            foundPatterns.add(linkName);
            break;
          }
        }
      }
    }

    // Persist to ref so links survive tab switches
    extractedLinksRef.current = extractedLinks;
    return extractedLinks;
  }, [logs]);

  // Notify parent when links are extracted (for cross-component sharing)
  useEffect(() => {
    if (onLinksExtracted && Object.keys(logExtractedLinks).length > 0) {
      onLinksExtracted(logExtractedLinks);
    }
  }, [logExtractedLinks, onLinksExtracted]);

  // Combine database links with log-extracted links
  // Use logExtractedLinksFromParent if provided (for info tab), otherwise use local extraction
  const combinedLinks = useMemo(() => {
    // Start with database links (they take priority if there's a conflict)
    const combined = { ...(links || {}) };
    // Use parent-provided links (for info tab) or locally extracted links (for logs tab)
    const extractedToUse = logExtractedLinksFromParent || logExtractedLinks;
    // Add log-extracted links (only if not already present)
    for (const [key, value] of Object.entries(extractedToUse)) {
      if (!combined[key]) {
        combined[key] = value;
      }
    }
    return combined;
  }, [links, logExtractedLinks, logExtractedLinksFromParent]);

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
        ) : (
          <LogFilter
            logs={logs}
            isLoading={isLoadingLogs && !hasReceivedLogChunk && !logs.length}
            selectedNode={selectedNode}
          />
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
        <div className="text-base mt-1 flex items-center gap-2">
          <span>
            {jobData.id} {jobData.name ? `(${jobData.name})` : ''}
          </span>
          {/* Badge for job group */}
          {jobData.is_job_group && (
            <span className="px-2 py-0.5 rounded text-xs font-medium bg-gray-200 text-gray-700">
              JobGroup
            </span>
          )}
        </div>
      </div>
      <div>
        <div className="text-gray-600 font-medium text-base">Status</div>
        <div className="text-base mt-1">
          <PluginSlot
            name="jobs.detail.status.badge"
            context={jobData}
            fallback={<StatusBadge status={computedStatus} />}
          />
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
          {allTasks.length > 1 ? (
            <NonCapitalizedTooltip
              content={`Aggregated from ${allTasks.length} tasks:\n${allTasks
                .map(
                  (task, index) =>
                    `Task ${index}${task.task ? ` (${task.task})` : ''}: ${task.requested_resources || task.resources_str || 'N/A'}`
                )
                .join('\n')}`}
              className="text-sm text-muted-foreground"
            >
              <span className="cursor-help border-b border-dotted border-gray-400">
                {(() => {
                  const resourcesList = allTasks
                    .map((t) => t.requested_resources || t.resources_str)
                    .filter(Boolean);
                  const uniqueResources = [...new Set(resourcesList)];
                  return uniqueResources.length === 1
                    ? `${uniqueResources[0]} (x${allTasks.length} tasks)`
                    : `${resourcesList[0]} (+${allTasks.length - 1} more)`;
                })()}
              </span>
            </NonCapitalizedTooltip>
          ) : (
            jobData.requested_resources || 'N/A'
          )}
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

      {/* External Links section - full width row */}
      <div className="col-span-2">
        <div className="text-gray-600 font-medium text-base">
          External Links
        </div>
        <div className="text-base mt-1">
          {combinedLinks && Object.keys(combinedLinks).length > 0 ? (
            <div className="flex flex-wrap gap-4">
              {Object.entries(combinedLinks).map(([label, url]) => {
                // Normalize URL - add https:// if no protocol specified
                const normalizedUrl =
                  url.startsWith('http://') || url.startsWith('https://')
                    ? url
                    : `https://${url}`;

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
              })}
            </div>
          ) : (
            <span className="text-gray-400">-</span>
          )}
        </div>
      </div>

      {/* Queue Details section - right column */}
      {jobData.details && (
        <PluginSlot
          name="jobs.detail.queue_details"
          context={{
            details: jobData.details,
            queueName: jobData.kueue_queue_name,
            infra: jobData.full_infra,
            jobData: jobData,
            title: 'Queue Details',
          }}
        />
      )}

      {/* Entrypoint section - full width row */}
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

            {/* Job YAML - Collapsible */}
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
                      // Build JobGroup header with name and execution
                      const hasJobGroupConfig =
                        jobData.name || jobData.execution;
                      const jobGroupHeader = hasJobGroupConfig
                        ? [
                            jobData.name ? `name: ${jobData.name}` : null,
                            jobData.execution
                              ? `execution: ${jobData.execution}`
                              : null,
                          ]
                            .filter(Boolean)
                            .join('\n') + '\n---\n'
                        : '';

                      if (yamlDocs.length === 0) {
                        return (
                          <div className="text-gray-500">No YAML available</div>
                        );
                      } else if (yamlDocs.length === 1) {
                        // Single document - show directly
                        return (
                          <YamlHighlighter className="whitespace-pre-wrap">
                            {jobGroupHeader + yamlDocs[0].content}
                          </YamlHighlighter>
                        );
                      } else {
                        // Multiple documents - show toggle and content
                        return (
                          <div className="space-y-4">
                            {/* Toggle for Full YAML vs Per-Job */}
                            <div className="flex items-center space-x-4 pb-2 border-b border-gray-200">
                              <button
                                onClick={() => setShowFullYaml(false)}
                                className={`text-sm px-2 py-1 rounded ${!showFullYaml ? 'bg-blue-100 text-blue-700' : 'text-gray-600 hover:text-gray-800'}`}
                              >
                                By Job
                              </button>
                              <button
                                onClick={() => setShowFullYaml(true)}
                                className={`text-sm px-2 py-1 rounded ${showFullYaml ? 'bg-blue-100 text-blue-700' : 'text-gray-600 hover:text-gray-800'}`}
                              >
                                Full YAML
                              </button>
                            </div>

                            {showFullYaml ? (
                              // Show full YAML with JobGroup header
                              <YamlHighlighter className="whitespace-pre-wrap">
                                {jobGroupHeader +
                                  yamlDocs
                                    .map((doc) => doc.content)
                                    .join('\n---\n')}
                              </YamlHighlighter>
                            ) : (
                              // Show per-job YAMLs
                              yamlDocs.map((doc, index) => (
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
                                        Job {index + 1}: {doc.preview}
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
                              ))
                            )}
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
  allTasks: PropTypes.array,
  activeTab: PropTypes.string,
  setIsLoadingLogs: PropTypes.func,
  setIsLoadingControllerLogs: PropTypes.func,
  isLoadingLogs: PropTypes.bool,
  isLoadingControllerLogs: PropTypes.bool,
  refreshFlag: PropTypes.number,
  poolsData: PropTypes.array,
  links: PropTypes.object,
  logExtractedLinks: PropTypes.object,
  onLinksExtracted: PropTypes.func,
  selectedTaskIndex: PropTypes.number,
  onTaskChange: PropTypes.func,
};

export default JobDetails;
