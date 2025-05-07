'use client';

import { useState, useEffect, useCallback } from 'react';
import { showToast } from '@/data/connectors/toast';
import { ENDPOINT } from '@/data/connectors/constants';

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
      return {
        status: clusterStatusMap[cluster.status],
        cluster: cluster.name,
        user: cluster.user_name,
        infra: cluster.cloud,
        region: cluster.region,
        cpus: cluster.cpus,
        mem: cluster.memory,
        gpus: cluster.accelerators,
        resources_str: cluster.resources_str,
        time: new Date(cluster.launched_at * 1000),
        num_nodes: cluster.nodes,
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

export async function streamClusterJobLogs({ clusterName, jobId, onNewLog }) {
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
  }, [cluster, job, fetchClusterData, fetchClusterJobData]);

  return { clusterData, clusterJobData, loading, refreshData };
}
