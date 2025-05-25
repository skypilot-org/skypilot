'use client';

import { useState, useEffect, useCallback } from 'react';
import { showToast } from '@/data/connectors/toast';
import { ENDPOINT } from '@/data/connectors/constants';

/**
 * Truncates a string in the middle, preserving parts from beginning and end.
 * @param {string} str - The string to truncate
 * @param {number} maxLength - Maximum length of the truncated string
 * @return {string} - Truncated string
 */
function truncateMiddle(str, maxLength = 15) {
  if (!str || str.length <= maxLength) return str;

  // Reserve 3 characters for '...'
  if (maxLength <= 3) return '...';

  // Calculate how many characters to keep from beginning and end
  const halfLength = Math.floor((maxLength - 3) / 2);
  const remainder = (maxLength - 3) % 2;

  // Keep one more character at the beginning if maxLength - 3 is odd
  const startLength = halfLength + remainder;
  const endLength = halfLength;

  // When endLength is 0, just show the start part and '...'
  if (endLength === 0) {
    return str.substring(0, startLength) + '...';
  }

  return (
    str.substring(0, startLength) +
    '...' +
    str.substring(str.length - endLength)
  );
}

const clusterStatusMap = {
  UP: 'RUNNING',
  STOPPED: 'STOPPED',
  INIT: 'LAUNCHING',
  null: 'TERMINATED',
};

export async function getClusters({ clusterNames = null } = {}) {
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
    // TODO(syang): remove X-Request-ID when v0.10.0 is released.
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('X-Request-ID');
    const fetchedData = await fetch(`${ENDPOINT}/api/get?request_id=${id}`);
    const data = await fetchedData.json();
    const clusters = data.return_value ? JSON.parse(data.return_value) : [];
    const clusterData = clusters.map((cluster) => {
      // Use cluster_hash for lookup, assuming it's directly in cluster.cluster_hash
      let region_or_zone = '';
      if (cluster.zone) {
        region_or_zone = cluster.zone;
      } else {
        region_or_zone = cluster.region;
      }
      // Store the full value before truncation
      const full_region_or_zone = region_or_zone;
      // Truncate region_or_zone in the middle if it's too long
      if (region_or_zone && region_or_zone.length > 25) {
        region_or_zone = truncateMiddle(region_or_zone, 25);
      }
      return {
        status: clusterStatusMap[cluster.status],
        cluster: cluster.name,
        user: cluster.user_name,
        user_hash: cluster.user_hash,
        cloud: cluster.cloud,
        region: cluster.region,
        infra: region_or_zone
          ? cluster.cloud + ' (' + region_or_zone + ')'
          : cluster.cloud,
        full_infra: full_region_or_zone
          ? `${cluster.cloud} (${full_region_or_zone})`
          : cluster.cloud,
        cpus: cluster.cpus,
        mem: cluster.memory,
        gpus: cluster.accelerators,
        resources_str: cluster.resources_str,
        resources_str_full: cluster.resources_str_full,
        time: new Date(cluster.launched_at * 1000),
        num_nodes: cluster.nodes,
        workspace: cluster.workspace,
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
  workspace,
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
        override_skypilot_config: {
          active_workspace: workspace || 'default',
        },
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

export async function getClusterJobs({ clusterName, workspace }) {
  try {
    const response = await fetch(`${ENDPOINT}/queue`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        cluster_name: clusterName,
        all_users: true,
        override_skypilot_config: {
          active_workspace: workspace,
        },
      }),
    });
    // TODO(syang): remove X-Request-ID when v0.10.0 is released.
    const id =
      response.headers.get('X-Skypilot-Request-ID') ||
      response.headers.get('X-Request-ID');
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
        status: job.status,
        job: job.job_name,
        user: job.username,
        gpus: job.accelerators || {},
        submitted_at: job.submitted_at
          ? new Date(job.submitted_at * 1000)
          : null,
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
        return data[0]; // Return the data for use in fetchClusterJobData
      } catch (error) {
        console.error('Error fetching cluster data:', error);
        return null;
      } finally {
        setLoadingClusterData(false);
      }
    }
    return null;
  }, [cluster]);

  const fetchClusterJobData = useCallback(
    async (workspace) => {
      if (cluster) {
        try {
          setLoadingClusterJobData(true);
          const data = await getClusterJobs({
            clusterName: cluster,
            workspace: workspace || 'default',
          });
          setClusterJobData(data);
        } catch (error) {
          console.error('Error fetching cluster job data:', error);
        } finally {
          setLoadingClusterJobData(false);
        }
      }
    },
    [cluster]
  );

  const refreshData = useCallback(async () => {
    const clusterInfo = await fetchClusterData();
    if (clusterInfo) {
      await fetchClusterJobData(clusterInfo.workspace);
    }
  }, [fetchClusterData, fetchClusterJobData]);

  useEffect(() => {
    const initializeData = async () => {
      const clusterInfo = await fetchClusterData();
      if (clusterInfo) {
        await fetchClusterJobData(clusterInfo.workspace);
      }
    };
    initializeData();
  }, [cluster, job, fetchClusterData, fetchClusterJobData]);

  return { clusterData, clusterJobData, loading, refreshData };
}
