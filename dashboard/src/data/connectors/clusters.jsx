'use client';

import { useState, useEffect, useCallback } from 'react';
import { showToast } from '@/data/connectors/toast';
import {
  ENDPOINT,
  ClusterDoesNotExist,
  NotSupportedError,
} from '@/data/connectors/constants';
import {
  isJobController,
  isSkyServeController,
  parseResources,
} from '@/data/utils';

const clusterStatusMap = {
  UP: 'RUNNING',
  STOPPED: 'STOPPED',
  INIT: 'LAUNCHING',
  null: 'TERMINATED',
};

export async function getClusters({ clusterNames = null } = {}) {
  // The URL returns a request id as a string, and we can further get
  // the actual data with http://xx.xx.xx.xx:xxxx/get with a json content
  // { "request_id": <id> }.
  try {
    const response = await fetch(`${ENDPOINT}/status`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        cluster_names: clusterNames,
        all_users: true,
      }),
    });
    const id = response.headers.get('x-request-id');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
    // print out the response for debugging
    const data = await fetchedData.json();
    const clusters = data.return_value ? JSON.parse(data.return_value) : [];
    const clusterData = clusters.map((cluster) => {
      const parsed_resources = parseResources(cluster.resources_str);
      const {
        infra,
        instance_type,
        cpus,
        mem,
        gpus,
        num_nodes,
        other_resources,
      } = parsed_resources.parsed_resources;
      return {
        state: clusterStatusMap[cluster.status],
        cluster: cluster.name,
        user: cluster.user_name,
        infra: infra,
        instance_type: instance_type,
        cpus: cpus,
        mem: mem,
        gpus: gpus,
        other_resources: other_resources,
        resources_str: cluster.resources_str,
        time: new Date(cluster.launched_at * 1000),
        num_nodes: num_nodes || 1,
        jobs: [],
        events: [
          {
            time: new Date(cluster.launched_at * 1000),
            event: 'Cluster created.',
          },
        ],
      };
    });
    return clusterData;
  } catch (error) {
    console.error('Error fetching clusters:', error);
    return [];
  }
}

export async function streamClusterJobLogs({
  clusterName,
  jobId,
  onNewLog,
}) {
  try {
    const response = await fetch(`${ENDPOINT}/logs`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        // TODO(hailong): set follow to true?
        // - Too much streaming requests may consume too much resources in api server
        // - Need to stop the api request in different cases
        follow: false,
        cluster_name: clusterName,
        job_id: jobId,
      }),
    });
    // Stream the logs
    const reader = response.body.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = new TextDecoder().decode(value);
      onNewLog(chunk);
    }
  } catch (error) {
    console.error('Error in streamClusterJobLogs:', error);
    showToast(`Error in streamClusterJobLogs: ${error.message}`, 'error');
  }
}

export async function getClusterJobs({ clusterName }) {
  try {
    const response = await fetch(`${ENDPOINT}/queue`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        cluster_name: clusterName,
        all_users: true,
      }),
    });
    const id = response.headers.get('x-request-id');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
    const data = await fetchedData.json();
    const jobs = JSON.parse(data.return_value);
    const jobData = jobs.map((job) => {
      let endTime = job.end_at ? job.end_at : Date.now() / 1000;
      let total_duration = 0;
      let job_duration = 0;
      if (job.submitted_at) {
        total_duration = endTime - job.submitted_at;
      }
      if (job.start_at) {
        job_duration = endTime - job.start_at;
      }

      return {
        id: job.job_id,
        state: job.status,
        job: job.job_name,
        user: job.username,
        gpus: job.accelerators || {},
        time: new Date(job.submitted_at * 1000),
        resources: job.resources,
        cluster: clusterName,
        total_duration: total_duration,
        job_duration: job_duration,
        infra: '',
        logs: '',
      };
    });
    return jobData;
  } catch (error) {
    console.error('Error fetching cluster jobs:', error);
    return [];
  }
}

export async function getClusterHistory() {
  try {
    const response = await fetch(`${ENDPOINT}/cost_report`);
    const id = response.headers.get('x-request-id');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
    const data = await fetchedData.json();
    const history = JSON.parse(data.return_value);
    const clusterHistory = history.map((cluster) => {
      // Only include clusters that have a null status
      // which means they are terminated
      if (cluster.status != null) {
        return null;
      }
      return {
        state: clusterStatusMap[cluster.status],
        cluster: cluster.name,
        user: cluster.user_hash,
        infra: cluster.cloud,
        instance_type: '-',
        other_resources: '-',
        gpus: cluster.accelerators || {},
        time: new Date(cluster.launched_at * 1000),
        num_nodes: cluster.num_nodes,
        jobs: [],
        events: [],
      };
    });
    // filter out nulls
    return clusterHistory.filter((cluster) => cluster !== null);
  } catch (error) {
    console.error('Error fetching cluster history:', error);
    return [];
  }
}

export function useClusterDetails({ cluster, job = null }) {
  const [clusterData, setClusterData] = useState(null);
  const [clusterJobData, setClusterJobData] = useState(null);
  const [loadingClusterData, setLoadingClusterData] = useState(true);
  const [loadingClusterJobData, setLoadingClusterJobData] = useState(true);

  const loading = loadingClusterData || loadingClusterJobData;

  const fetchClusterData = useCallback(async () => {
    if (cluster) {
      try {
        setLoadingClusterData(true);
        const data = await getClusters({ clusterNames: [cluster] });
        setClusterData(data[0]); // Assuming getClusters returns an array
      } catch (error) {
        console.error('Error fetching cluster data:', error);
      } finally {
        setLoadingClusterData(false);
      }
    }
  }, [cluster]);

  const fetchClusterJobData = useCallback(async () => {
    if (cluster) {
      try {
        setLoadingClusterJobData(true);
        const data = await getClusterJobs({ clusterName: cluster, job: job });
        setClusterJobData(data);
      } catch (error) {
        console.error('Error fetching cluster job data:', error);
      } finally {
        setLoadingClusterJobData(false);
      }
    }
  }, [cluster, job]);

  const refreshData = useCallback(async () => {
    await Promise.all([fetchClusterData(), fetchClusterJobData()]);
  }, [fetchClusterData, fetchClusterJobData]);

  useEffect(() => {
    fetchClusterData();
    fetchClusterJobData();

    // Set up an interval to refresh the data every 20 seconds
    const intervalId = setInterval(() => {
      fetchClusterData();
      fetchClusterJobData();
    }, 20000);

    // Clean up the interval on component unmount
    return () => clearInterval(intervalId);
  }, [cluster, job, fetchClusterData, fetchClusterJobData]);

  return { clusterData, clusterJobData, loading, refreshData };
}

export async function handleClusterAction(action, cluster, state) {
  let logStarter = '';
  let logMiddle = '';
  let apiPath = '';
  switch (action) {
    case 'stop':
      logStarter = 'Stopping';
      logMiddle = 'stopped';
      apiPath = 'stop';
      break;
    case 'terminate':
      logStarter = 'Terminating';
      logMiddle = 'terminated';
      apiPath = 'down';
      break;
    case 'start':
      logStarter = 'Starting';
      logMiddle = 'started';
      apiPath = 'start';
      break;
    default:
      throw new Error(`Invalid action: ${action}`);
  }

  let checkAPIPath = '';
  let checkRequestBody = {};
  if (state == 'RUNNING' && (action == 'stop' || action == 'terminate')) {
    if (isJobController(cluster)) {
      checkAPIPath = 'jobs/queue';
      checkRequestBody = {
        refresh: false,
        skip_finished: true,
        all_users: true,
      };
    } else if (isSkyServeController(cluster)) {
      // TODO(hailong): pre-check for serve controllers
    } else {
      checkAPIPath = 'queue';
      checkRequestBody = {
        cluster_name: cluster,
        skip_finished: true,
        all_users: true,
      };
    }
  }

  if (checkAPIPath) {
    try {
      const response = await fetch(`${ENDPOINT}/${checkAPIPath}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(checkRequestBody),
      });
      const id = response.headers.get('x-request-id');
      const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
      const data = await fetchedData.json();
      if (fetchedData.status !== 200) {
        showToast(
          `${logStarter} cluster ${cluster} failed: ${data.detail.error}`,
          'error'
        );
        return;
      }
      if (data.return_value) {
        const jobs = JSON.parse(data.return_value);
        if (jobs.length > 0) {
          if (jobs.length > 1) {
            showToast(
              `${logStarter} cluster ${cluster}, it has ${jobs.length} jobs running, please cancel them first.`,
              'warning'
            );
          } else {
            showToast(
              `${logStarter} cluster ${cluster}, it has 1 job running, please cancel it first.`,
              'warning'
            );
          }
          return;
        }
      }
    } catch (error) {
      console.error('Error in handleClusterAction:', error);
      showToast(
        `${logStarter} cluster ${cluster} error: ${error.message}`,
        'error'
      );
    }
  }

  // Show initial notification
  showToast(`${logStarter} cluster ${cluster}...`, 'info');

  try {
    try {
      const response = await fetch(`${ENDPOINT}/${apiPath}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          cluster_name: cluster,
        }),
      });

      const id = response.headers.get('x-request-id');
      const finalResponse = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);

      // Check the status code of the final response
      if (finalResponse.status === 200) {
        showToast(`Cluster ${cluster} ${logMiddle} successfully.`, 'success');
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
                    `${logStarter} cluster ${cluster} is not supported!`,
                    'error',
                    10000
                  );
                } else if (error.type && error.type === ClusterDoesNotExist) {
                  showToast(`Cluster ${cluster} does not exist.`, 'error');
                } else {
                  showToast(
                    `${logStarter} cluster ${cluster} failed: ${error.type}`,
                    'error'
                  );
                }
              } catch (jsonError) {
                showToast(
                  `${logStarter} cluster ${cluster} failed: ${data.detail.error}`,
                  'error'
                );
              }
            } else {
              showToast(
                `${logStarter} cluster ${cluster} failed with no details.`,
                'error'
              );
            }
          } catch (parseError) {
            showToast(
              `${logStarter} cluster ${cluster} failed with parse error.`,
              'error'
            );
          }
        } else {
          showToast(
            `${logStarter} cluster ${cluster} failed with status ${finalResponse.status}.`,
            'error'
          );
        }
      }
    } catch (fetchError) {
      console.error('Fetch error:', fetchError);
      showToast(
        `Network error ${logStarter} cluster ${cluster}: ${fetchError.message}`,
        'error'
      );
    }
  } catch (outerError) {
    console.error('Error in handleStop:', outerError);
    showToast(
      `Critical error ${logStarter} cluster ${cluster}: ${outerError.message}`,
      'error'
    );
  }
}

export async function handleStop(cluster, state) {
  await handleClusterAction('stop', cluster, state);
}

export async function handleTerminate(cluster, state) {
  await handleClusterAction('terminate', cluster, state);
}

export async function handleStart(cluster, state) {
  await handleClusterAction('start', cluster, state);
}
