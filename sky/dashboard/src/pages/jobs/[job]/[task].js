import React, { useState, useEffect } from 'react';
import { CircularProgress } from '@mui/material';
import { useRouter } from 'next/router';
import { Card } from '@/components/ui/card';
import { useSingleManagedJob, getPoolStatus } from '@/data/connectors/jobs';
import JobDetails from '../[job]';
import Link from 'next/link';
import {
  RotateCwIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  Download,
} from 'lucide-react';
import {
  CustomTooltip as Tooltip,
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
import { checkGrafanaAvailability } from '@/utils/grafana';
import { TelemetrySection } from '@/components/TelemetrySection';
import { hasAccelerator } from '@/utils/gpuUtils';
import { trackJobAction } from '@/lib/analytics';

function TaskDetails() {
  const router = useRouter();
  const { job: jobId, task: taskIndex } = router.query;
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  // Dynamic tasks (jobs launched from inside this job) are addressed as
  // /jobs/<root>/<index> too; their rows come with the job's own, and are
  // resolved below when the index is not one of the job's declared tasks.
  const {
    jobData,
    loading,
    members: treeMembers,
    membersLoaded: treeLoaded,
  } = useSingleManagedJob(jobId, refreshTrigger);
  const [poolsData, setPoolsData] = useState([]);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [refreshLogsFlag, setRefreshLogsFlag] = useState(0);
  const [isLogsExpanded, setIsLogsExpanded] = useState(true);
  const isMobile = useMobile();

  // GPU metrics state
  const [isGrafanaAvailable, setIsGrafanaAvailable] = useState(false);
  const [telemetryRefreshTrigger, setTelemetryRefreshTrigger] = useState(0);

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

  // Handle manual refresh
  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    try {
      setRefreshTrigger((prev) => prev + 1);
      setRefreshLogsFlag((prev) => prev + 1);
      setTelemetryRefreshTrigger((prev) => prev + 1);
    } catch (error) {
      console.error('Error refreshing data:', error);
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleLogsRefresh = () => {
    setRefreshLogsFlag((prev) => prev + 1);
  };

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
  if (taskData === null) {
    // Not one of the job's declared tasks: a dynamic task with this index? Its
    // page is the member job's page, headed as task <index> of this job.
    const member = treeMembers.find(
      (m) => String(m.dynamic_task_index) === String(taskIndexNum)
    );
    if (member) {
      // No launching task means the job was attached explicitly, from
      // outside the group (`--job-group`); the badge's hover says so. The
      // launching job is this job, so it is not named by id.
      const launchedFrom =
        member.parent_task_id != null
          ? `task ${member.parent_task_id} of ${
              String(member.parent_job_id) === String(jobId)
                ? 'this job'
                : `job ${member.parent_job_id}`
            }`
          : null;
      // The launching task, addressed the way the rest of the page
      // addresses tasks: a declared task by its task id, a dynamic task by
      // its dynamic index. The member's own job id stays out of view.
      let parentTask = null;
      if (String(member.parent_job_id) === String(jobId)) {
        if (member.parent_task_id != null) {
          parentTask = {
            label: String(member.parent_task_id),
            href: `/jobs/${jobId}/${member.parent_task_id}`,
          };
        }
      } else {
        const parentMember = treeMembers.find(
          (m) => String(m.id) === String(member.parent_job_id)
        );
        if (parentMember && parentMember.dynamic_task_index != null) {
          parentTask = {
            label: String(parentMember.dynamic_task_index),
            href: `/jobs/${jobId}/${parentMember.dynamic_task_index}`,
          };
        }
      }
      // The member's rows are already in the tree fetched above. Hand them
      // down so the task page does not fetch the same job again by id.
      const memberRows = treeMembers.filter(
        (m) => String(m.id) === String(member.id)
      );
      return (
        <JobDetails
          key={`dynamic-${member.id}`}
          overrideJobId={String(member.id)}
          preloaded={{
            jobs: memberRows,
            controllerStopped: jobData?.controllerStopped || false,
          }}
          onRefresh={handleManualRefresh}
          taskContext={{
            rootId: jobId,
            rootName: jobName,
            index: taskIndexNum,
            launchedFrom,
            parentTask,
          }}
        />
      );
    }
  }

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

        {(loading && isInitialLoad) || (taskData === null && !treeLoaded) ? (
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

            {/* Telemetry Section (GPU + CPU/Memory) - Show for Kubernetes tasks with cluster_name_on_cloud */}
            {isGrafanaAvailable &&
              taskData.full_infra?.toLowerCase().includes('kubernetes') &&
              !taskData.pool &&
              taskData.cluster_name_on_cloud && (
                <TelemetrySection
                  clusterNameOnCloud={taskData.cluster_name_on_cloud}
                  displayName={taskData.task || `Task ${taskIndex}`}
                  refreshTrigger={telemetryRefreshTrigger}
                  storageKey="skypilot-task-telemetry-expanded"
                  hasGpu={hasAccelerator(taskData.accelerators)}
                />
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
                            trackJobAction('download_logs', {
                              jobId,
                            });
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

// Same layout as the job page's info card: a label over each value, up to
// four columns, long values full width.
function TaskDetailsContent({ taskData, taskIndex, poolsData }) {
  const isEmpty = (v) =>
    v == null || v === '' || v === '-' || v === '–' || v === 'N/A';
  const dash = <span className="text-gray-400">-</span>;
  const show = (v) => (isEmpty(v) ? dash : v);
  const field = (label, value, { wide = false, newRow = false } = {}) => (
    <div
      key={label}
      className={`min-w-0 ${wide ? 'col-span-full' : ''} ${newRow ? 'min-[1400px]:col-start-1' : ''}`}
    >
      <div className="text-sm text-gray-500">{label}</div>
      <div className="text-base text-gray-900 mt-1 break-words">{value}</div>
    </div>
  );
  // 'Oct 6, 5:37:41 PM PDT': short enough to stay on one line in a cell.
  const shortTimestamp = (date) =>
    date
      ? date.toLocaleString('en-US', {
          month: 'short',
          day: 'numeric',
          hour: 'numeric',
          minute: '2-digit',
          second: '2-digit',
          timeZoneName: 'short',
        })
      : dash;
  const partition =
    taskData.cloud && taskData.cloud.toLowerCase() === 'slurm'
      ? taskData.zone
      : null;
  const infraContent = isEmpty(taskData.infra) ? (
    dash
  ) : (
    <NonCapitalizedTooltip
      content={taskData.full_infra || taskData.infra}
      className="text-sm text-muted-foreground"
    >
      <span>
        <Link href="/infra">
          {taskData.cloud || taskData.infra.split('(')[0].trim()}
        </Link>
        {taskData.infra.includes('(') &&
          ' ' + taskData.infra.substring(taskData.infra.indexOf('('))}
      </span>
    </NonCapitalizedTooltip>
  );
  // Base column count must not be grid-cols-2: a stylesheet loaded later can
  // redefine it and override the breakpoint variants.
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 min-[1400px]:grid-cols-4 gap-x-6 gap-y-5 [&_a]:text-blue-600 [&_a]:no-underline [&_a:hover]:underline">
      {field(
        'Task',
        <span>
          {taskIndex}
          {taskData.task ? ` (${taskData.task})` : ''}
        </span>
      )}
      {field(
        'Job',
        <Link href={`/jobs/${taskData.id}`}>
          {taskData.id}
          {taskData.name ? ` (${taskData.name})` : ''}
        </Link>
      )}
      {field('Status', <StatusBadge status={taskData.status} />)}
      {field(
        'Submitted',
        <span className="block min-w-0">
          <span className="block">{shortTimestamp(taskData.submitted_at)}</span>
          <span
            className="flex items-center min-w-0 text-gray-500"
            title={taskData.user}
          >
            <span className="flex-shrink-0 mr-1">by</span>
            <UserDisplay
              username={taskData.user}
              userHash={taskData.user_hash}
              className="flex items-center gap-1 min-w-0"
              linkClassName="block truncate min-w-0"
            />
          </span>
        </span>
      )}
      {field(
        'Workspace',
        <Link href="/workspaces">{taskData.workspace || 'default'}</Link>
      )}
      {field(
        'Duration',
        <span className="block">
          {taskData.started_at || taskData.job_duration > 0
            ? show(formatDuration(taskData.job_duration))
            : dash}
          {taskData.started_at && (
            <span className="block">
              <span className="text-gray-500">started at</span>{' '}
              {shortTimestamp(taskData.started_at)}
            </span>
          )}
        </span>
      )}
      {field('Recoveries', taskData.recoveries || 0)}
      <div
        key="divider-compute"
        className="hidden min-[1400px]:block col-span-full border-t border-gray-100"
      />
      {field(
        'Requested Resources',
        show(taskData.requested_resources || taskData.resources_str),
        { newRow: true }
      )}
      {field(
        'Infra',
        <span className="block">
          {infraContent}
          {partition && (
            <span className="block">
              <span className="text-gray-500">partition</span> {partition}
            </span>
          )}
        </span>
      )}
      {field(
        'Pool',
        isEmpty(taskData.pool)
          ? dash
          : renderPoolLink(taskData.pool, taskData.pool_hash, poolsData)
      )}
      {taskData.details &&
        field(
          'Details',
          <span className="whitespace-pre-wrap">{taskData.details}</span>,
          { wide: true }
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
