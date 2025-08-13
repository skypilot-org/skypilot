import { useState, useEffect } from 'react';
import { showToast } from '@/data/connectors/toast';
import {
  ENDPOINT,
  CLUSTER_NOT_UP_ERROR,
  CLUSTER_DOES_NOT_EXIST,
  NOT_SUPPORTED_ERROR,
} from '@/data/connectors/constants';
import dashboardCache from '@/lib/cache';
import { apiClient } from './client';

// Configuration
const DEFAULT_TAIL_LINES = 10000;

export async function getManagedJobs(options = {}) {
  try {
    const {
      allUsers = true,
      namePrefix,
      userPrefix,
      workspacePrefix,
      poolPrefix,
      offset,
      limit,
    } = options;

    const body = {
      all_users: allUsers,
      verbose: true,
    };
    if (namePrefix !== undefined) body.name_prefix = namePrefix;
    if (userPrefix !== undefined) body.user_prefix = userPrefix;
    if (workspacePrefix !== undefined) body.workspace_prefix = workspacePrefix;
    if (poolPrefix !== undefined) body.pool_prefix = poolPrefix;
    if (offset !== undefined) body.offset = offset;
    if (limit !== undefined) body.limit = limit;

    const response = await apiClient.post(`/jobs/queue`, body);
    const id = response.headers.get('X-Skypilot-Request-ID');
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            // Handle specific error types
            if (error.type && error.type === CLUSTER_NOT_UP_ERROR) {
              return { jobs: [], controllerStopped: true };
            }
          } catch (jsonError) {
            console.error('Error parsing JSON:', jsonError);
          }
        }
      } catch (parseError) {
        console.error('Error parsing JSON:', parseError);
      }
      return { jobs: [], controllerStopped: false };
    }
    // print out the response for debugging
    const data = await fetchedData.json();
    const parsed = data.return_value ? JSON.parse(data.return_value) : [];
    const managedJobs = Array.isArray(parsed) ? parsed : parsed?.jobs || [];
    const total = Array.isArray(parsed)
      ? managedJobs.length
      : (parsed?.total ?? managedJobs.length);

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

        if (cloud) {
          infra = cloud;
          if (region) {
            infra += `/${region}`;
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
        pool: job.pool,
        pool_hash: job.pool_hash,
        current_cluster_name: job.current_cluster_name,
        job_id_on_pool_cluster: job.job_id_on_pool_cluster,
      };
    });

    return { jobs: jobData, total, controllerStopped: false };
  } catch (error) {
    console.error('Error fetching managed job data:', error);
    return { jobs: [], controllerStopped: false };
  }
}

export async function getPoolStatus() {
  try {
    const response = await apiClient.post(`/jobs/pool_status`, {
      pool_names: null, // null means get all pools
    });
    const id = response.headers.get('X-Skypilot-Request-ID');
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);

    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            if (error.type && error.type === CLUSTER_NOT_UP_ERROR) {
              return { pools: [], controllerStopped: true };
            }
          } catch (jsonError) {
            console.error('Failed to parse error JSON:', jsonError);
          }
        }
      } catch (dataError) {
        console.error('Failed to parse response JSON:', dataError);
      }
      throw new Error('Server error');
    }

    // Parse the pools data from the response
    const data = await fetchedData.json();
    const poolData = data.return_value ? JSON.parse(data.return_value) : [];

    // Also fetch managed jobs to get job counts by pool
    let jobsData = { jobs: [] };
    try {
      const jobsResponse = await getManagedJobs({ allUsers: true });
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

export function useManagedJobDetails(refreshTrigger = 0) {
  const [jobData, setJobData] = useState(null);
  const [loadingJobData, setLoadingJobData] = useState(true);

  const loading = loadingJobData;

  useEffect(() => {
    async function fetchJobData() {
      try {
        setLoadingJobData(true);
        const data = await dashboardCache.get(getManagedJobs, [
          { allUsers: true },
        ]);
        setJobData(data);
      } catch (error) {
        console.error('Error fetching managed job data:', error);
      } finally {
        setLoadingJobData(false);
      }
    }

    fetchJobData();
  }, [refreshTrigger]);

  return { jobData, loading };
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
          { allUsers: true },
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
  const baseUrl = window.location.origin;
  const fullEndpoint = `${baseUrl}${ENDPOINT}`;

  // Create the fetch promise
  const fetchPromise = (async () => {
    try {
      const requestBody = {
        controller: controller,
        follow: false,
        job_id: jobId,
        tail: DEFAULT_TAIL_LINES,
      };

      const response = await fetch(`${fullEndpoint}/jobs/logs`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestBody),
        // Only use the signal if it's provided
        ...(signal ? { signal } : {}),
      });

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
      apiPath = 'jobs/queue';
      requestBody = { all_users: true, refresh: true };
      jobId = 'controller';
      break;
    default:
      throw new Error(`Invalid action: ${action}`);
  }

  // Show initial notification
  showToast(`${logStarter} job ${jobId}...`, 'info');

  const baseUrl = window.location.origin;
  const fullEndpoint = `${baseUrl}${ENDPOINT}`;

  try {
    try {
      const response = await fetch(`${fullEndpoint}/${apiPath}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestBody),
      });

      const id = response.headers.get('X-Skypilot-Request-ID');
      const finalResponse = await fetch(
        `${fullEndpoint}/api/get?request_id=${id}`
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
