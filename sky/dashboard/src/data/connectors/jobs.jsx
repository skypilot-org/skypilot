import { useState, useEffect, useCallback } from 'react';
import { showToast } from '@/data/connectors/toast';
import {
  CLUSTER_NOT_UP_ERROR,
  CLUSTER_DOES_NOT_EXIST,
  NOT_SUPPORTED_ERROR,
} from '@/data/connectors/constants';
import dashboardCache from '@/lib/cache';
import { apiClient } from './client';
import { applyEnhancements } from '@/plugins/dataEnhancement';

// ============ Pagination Plugin Integration ============

/**
 * Check if the jobs pagination plugin is available.
 * The plugin sets window.__skyJobsPaginationFetch when loaded.
 * With requires_early_init=True, the plugin is guaranteed to be
 * loaded before any API calls complete.
 */
function isJobsPaginationPluginAvailable() {
  return (
    typeof window !== 'undefined' &&
    typeof window.__skyJobsPaginationFetch === 'function'
  );
}

/**
 * Get the jobs pagination plugin fetch function
 */
function getJobsPaginationFetch() {
  return typeof window !== 'undefined' ? window.__skyJobsPaginationFetch : null;
}

// Configuration
const DEFAULT_TAIL_LINES = 5000;
const DEFAULT_FIELDS = [
  'job_id',
  '_job_id',
  'job_name',
  'user_name',
  'user_hash',
  'workspace',
  'submitted_at',
  'job_duration',
  'status',
  'resources',
  'cloud',
  'region',
  'accelerators',
  'cluster_resources',
  'cluster_resources_full',
  'recovery_count',
  'pool',
  'pool_hash',
  'details',
  'failure_reason',
  'links',
];

export async function getManagedJobs(options = {}) {
  try {
    const {
      allUsers = true,
      skipFinished = false,
      allFields = false,
      nameMatch,
      userMatch,
      workspaceMatch,
      poolMatch,
      page,
      limit,
      statuses,
      fields,
      jobIDs,
    } = options;

    const body = {
      all_users: allUsers,
      verbose: true,
      skip_finished: skipFinished,
    };
    if (nameMatch !== undefined) body.name_match = nameMatch;
    if (userMatch !== undefined) body.user_match = userMatch;
    if (workspaceMatch !== undefined) body.workspace_match = workspaceMatch;
    if (poolMatch !== undefined) body.pool_match = poolMatch;
    if (page !== undefined) body.page = page;
    if (limit !== undefined) body.limit = limit;
    if (statuses !== undefined && statuses.length > 0) body.statuses = statuses;
    if (jobIDs !== undefined && jobIDs.length > 0) body.job_ids = jobIDs;
    if (!allFields) {
      if (fields && fields.length > 0) {
        body.fields = fields;
      } else {
        body.fields = DEFAULT_FIELDS;
      }
    }

    const response = await apiClient.post(`/jobs/queue/v2`, body);
    if (!response.ok) {
      const msg = `Failed to get managed jobs with status ${response.status}`;
      throw new Error(msg);
    }
    const id = response.headers.get('X-Skypilot-Request-ID');
    // Handle empty request ID
    if (!id) {
      const msg = 'No request ID received from server for managed jobs';
      throw new Error(msg);
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    let errorMessage = fetchedData.statusText;
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            // Handle specific error types
            if (error.type && error.type === CLUSTER_NOT_UP_ERROR) {
              return { jobs: [], total: 0, controllerStopped: true };
            } else {
              errorMessage = error.message || String(data.detail.error);
            }
          } catch (jsonError) {
            console.error(
              'Error parsing JSON from data.detail.error:',
              jsonError
            );
            errorMessage = String(data.detail.error);
          }
        }
      } catch (parseError) {
        console.error('Error parsing response JSON:', parseError);
        errorMessage = String(parseError);
      }
    }
    // Handle all error status codes (4xx, 5xx, etc.)
    if (!fetchedData.ok) {
      const msg = `API request to get managed jobs result failed with status ${fetchedData.status}, error: ${errorMessage}`;
      throw new Error(msg);
    }
    // print out the response for debugging
    const data = await fetchedData.json();
    const parsed = data.return_value ? JSON.parse(data.return_value) : [];
    const managedJobs = Array.isArray(parsed) ? parsed : parsed?.jobs || [];
    const total = Array.isArray(parsed)
      ? managedJobs.length
      : (parsed?.total ?? managedJobs.length);
    const totalNoFilter = parsed?.total_no_filter || total;
    const statusCounts = parsed?.status_counts || {};

    // Process jobs data
    const jobData = managedJobs.map((job) => {
      let total_duration = 0;
      if (job.end_at && job.submitted_at) {
        total_duration = job.end_at - job.submitted_at;
      } else if (job.submitted_at) {
        total_duration = Date.now() / 1000 - job.submitted_at;
      }

      const events = [];
      if (job.submitted_at) {
        events.push({
          type: 'PENDING',
          timestamp: job.submitted_at,
        });
      }
      if (job.start_at) {
        events.push({
          type: 'RUNNING',
          timestamp: job.start_at,
        });
      }
      if (job.end_at) {
        events.push({
          type: job.status,
          timestamp: job.end_at,
        });
      }

      let cloud = '';
      let region = '';
      let cluster_resources = '';
      let infra = '';
      let full_infra = '';

      try {
        cloud = job.cloud || '';
        cluster_resources = job.cluster_resources;
        region = job.region || '';
        if (region === '-') {
          region = '';
        }

        if (cloud) {
          infra = cloud;
          if (region) {
            infra += ` (${region})`;
          }
        }

        full_infra = infra;
        if (job.accelerators) {
          const accel_str = Object.entries(job.accelerators)
            .map(([key, value]) => `${value}x${key}`)
            .join(', ');
          if (accel_str) {
            full_infra += ` (${accel_str})`;
          }
        }
      } catch (e) {
        cluster_resources = job.cluster_resources;
      }

      return {
        id: job.job_id,
        task_job_id: job._job_id,
        task: job.task_name,
        name: job.job_name,
        job_duration: job.job_duration,
        total_duration: total_duration,
        workspace: job.workspace,
        status: job.status,
        requested_resources: job.resources,
        resources_str: cluster_resources,
        resources_str_full: job.cluster_resources_full || cluster_resources,
        cloud: cloud,
        region: job.region,
        infra: infra,
        full_infra: full_infra,
        recoveries: job.recovery_count,
        details: job.details || job.failure_reason,
        user: job.user_name,
        user_hash: job.user_hash,
        submitted_at: job.submitted_at
          ? new Date(job.submitted_at * 1000)
          : null,
        events: events,
        dag_yaml: job.user_yaml,
        entrypoint: job.entrypoint,
        git_commit: job.metadata?.git_commit || '-',
        links: job.links || {},
        pool: job.pool,
        pool_hash: job.pool_hash,
        current_cluster_name: job.current_cluster_name,
        job_id_on_pool_cluster: job.job_id_on_pool_cluster,
        accelerators: job.accelerators, // Include accelerators field
        labels: job.labels || {}, // Include labels field
      };
    });

    // Apply plugin data enhancements
    // Pass raw backend data so enhancements can extract fields directly
    const enhancedJobs = await applyEnhancements(jobData, 'jobs', {
      dashboardCache,
      rawData: managedJobs, // Raw backend response for field extraction
    });

    return {
      jobs: enhancedJobs,
      total,
      totalNoFilter,
      controllerStopped: false,
      statusCounts,
    };
  } catch (error) {
    console.error('Error fetching managed job data:', error);
    // Signal to the cache to not overwrite previously cached data
    throw error;
  }
}

/**
 * Enhanced getManagedJobs function that supports client-side pagination
 * This function fetches all jobs data once and caches it, then performs filtering and pagination on the client side
 * @param {Object} options - Query options
 * @param {boolean} options.allUsers - Whether to fetch jobs for all users
 * @param {string} options.nameMatch - Filter by job name
 * @param {string} options.userMatch - Filter by user
 * @param {string} options.workspaceMatch - Filter by workspace
 * @param {string} options.poolMatch - Filter by pool
 * @param {Array} options.jobIDs - Filter by job IDs
 * @param {number} options.page - Page page (1-based)
 * @param {number} options.limit - Page size
 * @param {Array} options.fields - Fields to return
 * @param {boolean} options.allFields - Whether to return all fields (default: false)
 * @param {boolean} options.useClientPagination - Whether to use client-side pagination (default: true)
 * @returns {Promise<{jobs: Array, total: number, controllerStopped: boolean, __skipCache?: boolean}>}
 */
export async function getManagedJobsWithClientPagination(options) {
  const {
    allUsers = true,
    nameMatch,
    userMatch,
    workspaceMatch,
    poolMatch,
    page = 1,
    limit = 10,
    jobIDs,
    fields,
    allFields = false,
    useClientPagination = true,
  } = options || {};

  try {
    // If client pagination is disabled, fall back to server-side pagination
    if (!useClientPagination) {
      return await getManagedJobs(options);
    }

    // Create cache key for full dataset (without pagination params)
    const cacheKey = {
      allUsers,
      nameMatch,
      userMatch,
      workspaceMatch,
      poolMatch,
      jobIDs,
      fields,
      allFields,
    };

    // Fetch all data without pagination parameters
    const fullDataResponse = await getManagedJobs(cacheKey);

    if (fullDataResponse.controllerStopped || !fullDataResponse.jobs) {
      return fullDataResponse;
    }

    const allJobs = fullDataResponse.jobs;
    const total = allJobs.length;

    // Apply client-side pagination
    const startIndex = (page - 1) * limit;
    const endIndex = startIndex + limit;
    const paginatedJobs = allJobs.slice(startIndex, endIndex);

    return {
      jobs: paginatedJobs,
      total: total,
      controllerStopped: false,
    };
  } catch (error) {
    console.error(
      'Error fetching managed job data with client pagination:',
      error
    );
    throw error;
  }
}

export async function getPoolStatus() {
  try {
    const response = await apiClient.post(`/jobs/pool_status`, {
      pool_names: null, // null means get all pools
    });
    if (!response.ok) {
      const msg = `Initial API request to get pool status failed with status ${response.status}`;
      throw new Error(msg);
    }
    const id = response.headers.get('X-Skypilot-Request-ID');
    if (!id) {
      const msg = 'No request ID received from server for getting pool status';
      throw new Error(msg);
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    let errorMessage = fetchedData.statusText;
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            if (error.type && error.type === CLUSTER_NOT_UP_ERROR) {
              return { pools: [], controllerStopped: true };
            } else {
              errorMessage = error.message || String(data.detail.error);
            }
          } catch (jsonError) {
            console.error(
              'Error parsing JSON from data.detail.error:',
              jsonError
            );
            errorMessage = String(data.detail.error);
          }
        }
      } catch (dataError) {
        console.error('Error parsing response JSON:', dataError);
        errorMessage = String(dataError);
      }
    }

    if (!fetchedData.ok) {
      const msg = `API request to get pool status result failed with status ${fetchedData.status}, error: ${errorMessage}`;
      throw new Error(msg);
    }

    // Parse the pools data from the response
    const data = await fetchedData.json();
    const poolData = data.return_value ? JSON.parse(data.return_value) : [];

    // Also fetch managed jobs to get job counts by pool
    let jobsData = { jobs: [] };
    try {
      const jobsResponse = await dashboardCache.get(getManagedJobs, [
        {
          allUsers: true,
          skipFinished: true,
          fields: ['pool', 'status'],
        },
      ]);
      if (!jobsResponse.controllerStopped) {
        jobsData = jobsResponse;
      }
    } catch (jobsError) {
      console.warn('Failed to fetch jobs for pool job counts:', jobsError);
    }

    // Process job counts by pool and status
    const jobCountsByPool = {};
    const terminalStatuses = [
      'SUCCEEDED',
      'FAILED',
      'FAILED_SETUP',
      'FAILED_PRECHECKS',
      'FAILED_NO_RESOURCE',
      'FAILED_CONTROLLER',
      'CANCELLED',
    ];

    if (jobsData.jobs && Array.isArray(jobsData.jobs)) {
      jobsData.jobs.forEach((job) => {
        const poolName = job.pool;
        const status = job.status;

        if (poolName && !terminalStatuses.includes(status)) {
          if (!jobCountsByPool[poolName]) {
            jobCountsByPool[poolName] = {};
          }
          jobCountsByPool[poolName][status] =
            (jobCountsByPool[poolName][status] || 0) + 1;
        }
      });
    }

    // Add job counts to each pool
    const pools = poolData.map((pool) => ({
      ...pool,
      jobCounts: jobCountsByPool[pool.name] || {},
    }));

    return { pools, controllerStopped: false };
  } catch (error) {
    console.error('Error fetching pools:', error);
    throw error;
  }
}

// Hook for individual job details that reuses the main jobs cache
export function useSingleManagedJob(jobId, refreshTrigger = 0) {
  const [jobData, setJobData] = useState(null);
  const [loadingJobData, setLoadingJobData] = useState(true);

  const loading = loadingJobData;

  useEffect(() => {
    async function fetchJobData() {
      if (!jobId) return;

      try {
        setLoadingJobData(true);

        // Always get all jobs data (cache handles freshness automatically)
        const allJobsData = await dashboardCache.get(getManagedJobs, [
          { allUsers: true, allFields: true, jobIDs: [jobId] },
        ]);

        // Filter for the specific job client-side
        const job = allJobsData?.jobs?.find(
          (j) => String(j.id) === String(jobId)
        );

        if (job) {
          setJobData({
            jobs: [job],
            controllerStopped: allJobsData.controllerStopped || false,
          });
        } else {
          // Job not found in the results
          setJobData({
            jobs: [],
            controllerStopped: allJobsData.controllerStopped || false,
          });
        }
      } catch (error) {
        console.error('Error fetching single managed job data:', error);
        setJobData({ jobs: [], controllerStopped: false });
      } finally {
        setLoadingJobData(false);
      }
    }

    fetchJobData();
  }, [jobId, refreshTrigger]);

  return { jobData, loading };
}

export async function streamManagedJobLogs({
  jobId,
  controller = false,
  signal,
  onNewLog,
}) {
  // Measure timeout from last received data, not from start of request.
  const inactivityTimeout = 30000; // 30 seconds of no data activity
  let lastActivity = Date.now();
  let timeoutId;

  // Create an activity-based timeout promise
  const createTimeoutPromise = () => {
    return new Promise((resolve) => {
      const checkActivity = () => {
        const timeSinceLastActivity = Date.now() - lastActivity;

        if (timeSinceLastActivity >= inactivityTimeout) {
          resolve({ timeout: true });
        } else {
          // Check again after remaining time
          timeoutId = setTimeout(
            checkActivity,
            inactivityTimeout - timeSinceLastActivity
          );
        }
      };

      timeoutId = setTimeout(checkActivity, inactivityTimeout);
    });
  };

  const timeoutPromise = createTimeoutPromise();

  // Create the fetch promise
  const fetchPromise = (async () => {
    try {
      const requestBody = {
        controller: controller,
        follow: false,
        job_id: jobId,
        tail: DEFAULT_TAIL_LINES,
      };

      const response = await apiClient.fetchImmediate(
        '/jobs/logs',
        requestBody,
        'POST',
        { signal }
      );

      // Stream the logs
      const reader = response.body.getReader();

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          // Update activity timestamp when we receive data
          lastActivity = Date.now();

          const chunk = new TextDecoder().decode(value);
          onNewLog(chunk);
        }
      } finally {
        // Only cancel the reader if the signal hasn't been aborted
        // If signal is aborted, the reader should already be canceling
        if (!signal || !signal.aborted) {
          try {
            reader.cancel();
          } catch (cancelError) {
            // Ignore errors from reader cancellation
            if (cancelError.name !== 'AbortError') {
              console.warn('Error canceling reader:', cancelError);
            }
          }
        }
        // Clear the timeout when streaming completes successfully
        if (timeoutId) {
          clearTimeout(timeoutId);
        }
      }
      return { timeout: false };
    } catch (error) {
      // Clear timeout on any error
      if (timeoutId) {
        clearTimeout(timeoutId);
      }

      // If this was an abort, just return silently
      if (error.name === 'AbortError') {
        return { timeout: false };
      }
      throw error;
    }
  })();

  // Race the fetch against the activity-based timeout
  const result = await Promise.race([fetchPromise, timeoutPromise]);

  // Clear any remaining timeout
  if (timeoutId) {
    clearTimeout(timeoutId);
  }

  // If we timed out due to inactivity, show a more informative message
  if (result.timeout) {
    showToast(
      `Log request for job ${jobId} timed out after ${inactivityTimeout / 1000}s of inactivity`,
      'warning'
    );
    return;
  }
}

export async function handleJobAction(action, jobId, cluster) {
  let logStarter = '';
  let logMiddle = '';
  let apiPath = '';
  let requestBody = {};
  switch (action) {
    case 'restartcontroller':
      logStarter = 'Restarting';
      logMiddle = 'restarted';
      apiPath = 'jobs/queue/v2';
      requestBody = {
        all_users: true,
        refresh: true,
        skip_finished: true,
        fields: ['status'],
      };
      jobId = 'controller';
      break;
    default:
      throw new Error(`Invalid action: ${action}`);
  }

  // Show initial notification
  showToast(`${logStarter} job ${jobId}...`, 'info');

  try {
    try {
      const response = await apiClient.fetchImmediate(
        `/${apiPath}`,
        requestBody
      );
      if (!response.ok) {
        console.error(
          `Initial API request ${apiPath} failed with status ${response.status}`
        );
        showToast(
          `${logStarter} job ${jobId} failed with status ${response.status}.`,
          'error'
        );
        return;
      }

      const id = response.headers.get('X-Skypilot-Request-ID');
      if (!id) {
        console.error(`No request ID received from server for ${apiPath}`);
        showToast(
          `${logStarter} job ${jobId} failed with no request ID.`,
          'error'
        );
        return;
      }
      const finalResponse = await apiClient.fetchImmediate(
        `/api/get?request_id=${id}`,
        undefined,
        'GET'
      );

      // Check the status code of the final response
      if (finalResponse.status === 200) {
        showToast(`Job ${jobId} ${logMiddle} successfully.`, 'success');
      } else {
        if (finalResponse.status === 500) {
          try {
            const data = await finalResponse.json();

            if (data.detail && data.detail.error) {
              try {
                const error = JSON.parse(data.detail.error);

                // Handle specific error types
                if (error.type && error.type === NOT_SUPPORTED_ERROR) {
                  showToast(
                    `${logStarter} job ${jobId} is not supported!`,
                    'error',
                    10000
                  );
                } else if (
                  error.type &&
                  error.type === CLUSTER_DOES_NOT_EXIST
                ) {
                  showToast(`Cluster ${cluster} does not exist.`, 'error');
                } else if (error.type && error.type === CLUSTER_NOT_UP_ERROR) {
                  showToast(`Cluster ${cluster} is not up.`, 'error');
                } else {
                  showToast(
                    `${logStarter} job ${jobId} failed: ${error.type}`,
                    'error'
                  );
                }
              } catch (jsonError) {
                showToast(
                  `${logStarter} job ${jobId} failed: ${data.detail.error}`,
                  'error'
                );
              }
            } else {
              showToast(
                `${logStarter} job ${jobId} failed with no details.`,
                'error'
              );
            }
          } catch (parseError) {
            showToast(
              `${logStarter} job ${jobId} failed with parse error.`,
              'error'
            );
          }
        } else {
          showToast(
            `${logStarter} job ${jobId} failed with status ${finalResponse.status}.`,
            'error'
          );
        }
      }
    } catch (fetchError) {
      console.error('Fetch error:', fetchError);
      showToast(
        `Network error ${logStarter} job ${jobId}: ${fetchError.message}`,
        'error'
      );
    }
  } catch (outerError) {
    console.error('Error in handleStop:', outerError);
    showToast(
      `Critical error ${logStarter} job ${jobId}: ${outerError.message}`,
      'error'
    );
  }
}

/**
 * Downloads managed job logs as a zip via the API server.
 * Flow:
 * 1) POST /jobs/download_logs to fetch logs from the remote cluster to API server
 * 2) POST /download to stream a zip back to the browser and trigger download
 */
export async function downloadManagedJobLogs({
  jobId = null,
  name = null,
  controller = false,
}) {
  try {
    // Step 1: schedule server-side download; result is a mapping job_id -> folder path on API server
    const mapping = await apiClient.fetch('/jobs/download_logs', {
      job_id: jobId,
      name: name,
      controller: controller,
      refresh: false,
    });

    const folderPaths = Object.values(mapping || {});
    if (!folderPaths.length) {
      showToast('No logs found to download.', 'warning');
      return;
    }

    // Step 2: request the zip and trigger browser download
    const resp = await apiClient.fetchImmediate('/download?relative=items', {
      folder_paths: folderPaths,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Download failed: ${resp.status} ${text}`);
    }
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    const ts = new Date().toISOString().replace(/[:.]/g, '-');
    const namePart = jobId ? `job-${jobId}` : name ? `job-${name}` : 'job';
    const logType = controller ? 'controller-logs' : 'logs';
    a.href = url;
    a.download = `managed-${namePart}-${logType}-${ts}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (error) {
    console.error('Error downloading managed job logs:', error);
    showToast(`Error downloading managed job logs: ${error.message}`, 'error');
  }
}

// ============ useJobsData Hook ============

/**
 * Hook for jobs data with pagination support.
 * If the pagination plugin is available, uses server-side pagination.
 * Otherwise, falls back to client-side pagination with getManagedJobs.
 *
 * With requires_early_init=True, the plugin is guaranteed to be loaded
 * before the first API call completes, so we just need a simple check.
 *
 * @param {Object} options - Hook options
 * @param {number} options.refreshInterval - Auto-refresh interval in ms
 * @param {Object} options.sortConfig - Sort configuration { key, direction }
 * @param {Array} options.filters - Array of filter objects
 * @param {Array} options.statuses - Array of status strings to filter by
 * @returns {Object} Jobs data with pagination state and actions
 */
export function useJobsData(options = {}) {
  const {
    refreshInterval = null,
    sortConfig = { key: null, direction: 'ascending' },
    filters = [],
    statuses = [],
  } = options;

  // Convert sortConfig to API format
  // Default to submitted_at desc (newest first) when no sort is specified
  const sortBy = sortConfig.key || 'submitted_at';
  const sortOrder = sortConfig.key
    ? sortConfig.direction === 'ascending'
      ? 'asc'
      : 'desc'
    : 'desc';

  // Serialize filters for stable dependency comparison
  const filtersKey = JSON.stringify(filters);
  const statusesKey = JSON.stringify(statuses);

  const [data, setData] = useState([]);
  const [fullData, setFullData] = useState([]); // Full dataset for client-side filtering
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(10);
  const [total, setTotal] = useState(0);
  const [totalNoFilter, setTotalNoFilter] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [hasNext, setHasNext] = useState(false);
  const [hasPrev, setHasPrev] = useState(false);
  const [error, setError] = useState(null);
  const [isServerPagination, setIsServerPagination] = useState(false);
  const [controllerStopped, setControllerStopped] = useState(false);
  const [statusCounts, setStatusCounts] = useState({});

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [filtersKey, statusesKey]);

  /**
   * Fetch jobs using server-side pagination (plugin path)
   */
  const fetchServerSide = useCallback(async () => {
    console.log('[useJobsData] Using server-side pagination');
    const pluginFetch = getJobsPaginationFetch();

    const result = await dashboardCache.get(pluginFetch, [
      {
        page,
        limit,
        sortBy,
        sortOrder,
        filters,
        statuses,
      },
    ]);

    // Handle controller stopped
    if (result.controllerStopped) {
      setControllerStopped(true);
      setData([]);
      setFullData([]);
      setTotal(0);
      setTotalPages(0);
      return;
    }

    const resultTotal = result.total || 0;
    const resultTotalNoFilter = result.totalNoFilter || resultTotal;
    const resultTotalPages = result.totalPages || result.total_pages || 1;
    const resultHasNext = result.hasNext || result.has_next || false;
    const resultHasPrev = result.hasPrev || result.has_prev || false;
    const resultData = result.items || result.data || [];

    setData(resultData);
    setFullData(resultData);
    setTotal(resultTotal);
    setTotalNoFilter(resultTotalNoFilter);
    setTotalPages(resultTotalPages);
    setHasNext(resultHasNext);
    setHasPrev(resultHasPrev);
    setIsServerPagination(true);
    setControllerStopped(false);
    setStatusCounts(result.statusCounts || {});

    // Prefetch next page in background if there is one
    if (resultHasNext) {
      const nextPageOptions = {
        page: page + 1,
        limit,
        sortBy,
        sortOrder,
        filters,
        statuses,
      };
      dashboardCache
        .get(pluginFetch, [nextPageOptions])
        .then(() => console.log('[useJobsData] Prefetched page', page + 1))
        .catch((err) => console.warn('[useJobsData] Prefetch failed:', err));
    }
  }, [page, limit, sortBy, sortOrder, filters, statuses]);

  /**
   * Fetch jobs using client-side pagination (default path)
   */
  const fetchClientSide = useCallback(async () => {
    console.log('[useJobsData] Using client-side pagination');

    const response = await dashboardCache.get(getManagedJobs, [
      { allUsers: true },
    ]);

    // Handle controller stopped
    if (response.controllerStopped) {
      setControllerStopped(true);
      setData([]);
      setFullData([]);
      setTotal(0);
      setTotalPages(0);
      return;
    }

    const allJobs = response.jobs || [];

    // Client-side pagination
    const clientTotal = allJobs.length;
    const clientTotalPages = Math.ceil(clientTotal / limit) || 1;
    const startIndex = (page - 1) * limit;
    const paginatedData = allJobs.slice(startIndex, startIndex + limit);

    setData(paginatedData);
    setFullData(allJobs);
    setTotal(clientTotal);
    setTotalNoFilter(response.totalNoFilter || clientTotal);
    setTotalPages(clientTotalPages);
    setHasNext(page < clientTotalPages);
    setHasPrev(page > 1);
    setIsServerPagination(false);
    setControllerStopped(false);
    setStatusCounts(response.statusCounts || {});
  }, [page, limit]);

  /**
   * Main fetch function - chooses server or client path
   */
  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      if (isJobsPaginationPluginAvailable()) {
        await fetchServerSide();
      } else {
        await fetchClientSide();
      }
    } catch (fetchError) {
      console.error('[useJobsData] Error fetching jobs:', fetchError);
      setError(fetchError);
      setData([]);
      setFullData([]);
    }

    setLoading(false);
  }, [fetchServerSide, fetchClientSide]);

  // Fetch data on mount and when dependencies change
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Auto-refresh
  useEffect(() => {
    if (!refreshInterval) return;
    const interval = setInterval(() => {
      if (document.visibilityState === 'visible') {
        fetchData();
      }
    }, refreshInterval);
    return () => clearInterval(interval);
  }, [refreshInterval, fetchData]);

  // Handle limit change - reset to page 1
  const handleSetLimit = useCallback((newLimit) => {
    setLimit(newLimit);
    setPage(1);
  }, []);

  return {
    // Data - current page slice (paginated)
    data,
    // allData - full dataset for client-side filtering (in server mode, same as data)
    allData: fullData,
    total,
    totalNoFilter,
    statusCounts,

    // Pagination state
    page,
    limit,
    totalPages,
    hasNext,
    hasPrev,

    // Pagination actions
    setPage,
    setLimit: handleSetLimit,

    // Other
    loading,
    error,
    refresh: fetchData,
    isServerPagination,
    controllerStopped,
  };
}
