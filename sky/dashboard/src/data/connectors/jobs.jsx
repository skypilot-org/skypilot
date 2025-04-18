import { useState, useEffect } from 'react';
import { showToast } from '@/data/connectors/toast';
import {
  ENDPOINT,
  NotSupportedError,
  ClusterDoesNotExist,
  ClusterNotUpError,
} from '@/data/connectors/constants';

export async function getManagedJobs({ allUsers = true } = {}) {
  try {
    const response = await fetch(`${ENDPOINT}/jobs/queue`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        all_users: allUsers,
      }),
    });
    const id = response.headers.get('x-request-id');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
    if (fetchedData.status === 500) {
      try {
        const data = await fetchedData.json();
        if (data.detail && data.detail.error) {
          try {
            const error = JSON.parse(data.detail.error);
            // Handle specific error types
            if (error.type && error.type === ClusterNotUpError) {
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
    const managedJobs = data.return_value ? JSON.parse(data.return_value) : [];
    const jobData = managedJobs.map((job) => {
      // Create events array correctly
      const events = [];
      if (job.submitted_at) {
        events.push({
          time: new Date(job.submitted_at * 1000),
          event: 'Job submitted.',
        });
      }
      if (job.start_at) {
        events.push({
          time: new Date(job.start_at * 1000),
          event: 'Job started.',
        });
      }

      // Add completed event if end_at exists
      if (job.end_at) {
        if (job.status == 'CANCELLING' || job.status == 'CANCELLED') {
          events.push({
            time: new Date(job.end_at * 1000),
            event: 'Job cancelled.',
          });
        } else {
          events.push({
            time: new Date(job.end_at * 1000),
            event: 'Job completed.',
          });
        }
      }
      if (job.last_recovered_at && job.last_recovered_at != job.start_at) {
        events.push({
          time: new Date(job.last_recovered_at * 1000),
          event: 'Job recovered.',
        });
      }

      let endTime = job.end_at ? job.end_at : Date.now() / 1000;
      const total_duration = endTime - job.submitted_at;

      return {
        id: job.job_id,
        task: job.task_name,
        name: job.job_name,
        job_duration: job.job_duration,
        total_duration: total_duration,
        status: job.status,
        resources: job.resources,
        cluster: job.cluster_resources,
        region: job.region,
        recoveries: job.recovery_count,
        details: job.failure_reason,
        user: job.user_name,
        submitted_at: job.submitted_at
          ? new Date(job.submitted_at * 1000)
          : null,
        events: events,
      };
    });
    return { jobs: jobData, controllerStopped: false };
  } catch (error) {
    console.error('Error fetching managed job data:', error);
    return { jobs: [], controllerStopped: false };
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
        const data = await getManagedJobs({ allUsers: true });
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

export async function streamManagedJobLogs({
  jobId,
  controller = false,
  signal,
  onNewLog,
}) {
  const timeout = 10000; // Default timeout of 10 seconds

  // Create a timeout promise that resolves after the specified time
  const timeoutPromise = new Promise((resolve) => {
    setTimeout(() => {
      resolve({ timeout: true });
    }, timeout);
  });

  // Create the fetch promise
  const fetchPromise = (async () => {
    try {
      const response = await fetch(`${ENDPOINT}/jobs/logs`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          controller: controller,
          follow: false,
          job_id: jobId,
        }),
        // Only use the signal if it's provided
        ...(signal ? { signal } : {}),
      });

      // Stream the logs
      const reader = response.body.getReader();

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = new TextDecoder().decode(value);
          onNewLog(chunk);
        }
      } finally {
        reader.cancel();
      }
      return { timeout: false };
    } catch (error) {
      // If this was an abort, just return silently
      if (error.name === 'AbortError') {
        return { timeout: false };
      }
      throw error;
    }
  })();

  // Race the fetch against the timeout
  const result = await Promise.race([fetchPromise, timeoutPromise]);
  // If we timed out, just return silently without throwing an error
  if (result.timeout) {
    showToast(
      `Log request for job ${jobId} timed out after ${timeout}ms`,
      'error'
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

  try {
    try {
      const response = await fetch(`${ENDPOINT}/${apiPath}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestBody),
      });

      const id = response.headers.get('x-request-id');
      const finalResponse = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);

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
                if (error.type && error.type === NotSupportedError) {
                  showToast(
                    `${logStarter} job ${jobId} is not supported!`,
                    'error',
                    10000
                  );
                } else if (error.type && error.type === ClusterDoesNotExist) {
                  showToast(`Cluster ${cluster} does not exist.`, 'error');
                } else if (error.type && error.type === ClusterNotUpError) {
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
