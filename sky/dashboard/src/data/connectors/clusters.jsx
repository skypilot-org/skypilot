'use client';

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { showToast } from '@/data/connectors/toast';
import { apiClient } from '@/data/connectors/client';
import { ENDPOINT } from '@/data/connectors/constants';
import dashboardCache from '@/lib/cache';
import { applyEnhancements } from '@/plugins/dataEnhancement';

// ============ Pagination Plugin Integration ============

/**
 * Check if the pagination plugin is available.
 * The plugin sets window.__skyPaginationFetch when loaded.
 * With requires_early_init=True, the plugin is guaranteed to be
 * loaded before any API calls complete.
 */
function isPaginationPluginAvailable() {
  return (
    typeof window !== 'undefined' &&
    typeof window.__skyPaginationFetch === 'function'
  );
}

/**
 * Get the pagination plugin fetch function
 */
function getPaginationFetch() {
  return typeof window !== 'undefined' ? window.__skyPaginationFetch : null;
}

const DEFAULT_TAIL_LINES = 5000;

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
  PENDING: 'PENDING',
  null: 'TERMINATED',
};

export async function getClusters({ clusterNames = null } = {}) {
  try {
    const clusters = await apiClient.fetch('/status', {
      cluster_names: clusterNames,
      all_users: true,
      include_credentials: false,
      include_handle: false,
      summary_response: clusterNames == null,
    });

    const clusterData = clusters.map((cluster) => {
      // Use cluster_hash for lookup, assuming it's directly in cluster.cluster_hash
      let region_or_zone = '';
      if (cluster.zone) {
        region_or_zone = cluster.zone;
      } else {
        region_or_zone = cluster.region;
      }
      // For SSH Node Pools, strip the 'ssh-' prefix from region display
      // to avoid redundant "SSH (ssh-poolname)" showing as "SSH (poolname)"
      if (cluster.cloud === 'SSH' && region_or_zone?.startsWith('ssh-')) {
        region_or_zone = region_or_zone.substring(4);
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
        cluster_hash: cluster.cluster_hash,
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
        autostop: cluster.autostop,
        last_event: cluster.last_event,
        to_down: cluster.to_down,
        cluster_name_on_cloud: cluster.cluster_name_on_cloud,
        labels: cluster.labels || {},
        node_names: cluster.node_names || null,
        jobs: [],
        command: cluster.last_creation_command || cluster.last_use,
        task_yaml: cluster.last_creation_yaml || '{}',
        events: [
          {
            time: new Date(cluster.launched_at * 1000),
            event: 'Cluster created.',
          },
        ],
      };
    });

    // Apply plugin data enhancements
    // Pass raw backend data so enhancements can extract fields directly
    const enhancedClusters = await applyEnhancements(clusterData, 'clusters', {
      dashboardCache,
      rawData: clusters, // Raw backend response for field extraction
    });

    return enhancedClusters;
  } catch (error) {
    console.error('Error fetching clusters:', error);
    throw error;
  }
}

export async function getClusterHistory(clusterHash = null, days = 30) {
  try {
    const requestBody = {
      days: days,
      dashboard_summary_response: true,
    };

    // If a specific cluster hash is provided, include it in the request
    if (clusterHash) {
      requestBody.cluster_hashes = [clusterHash];
    }

    const history = await apiClient.fetch('/cost_report', requestBody);

    const historyData = history.map((cluster) => {
      // Get cloud name from resources if available
      let cloud = 'Unknown';
      if (cluster.cloud) {
        cloud = cluster.cloud;
      } else if (cluster.resources && cluster.resources.cloud) {
        cloud = cluster.resources.cloud;
      }

      // Get user name - need to look up from user_hash if needed
      let user_name = cluster.user_name || '-';

      // Extract resource info

      return {
        status: cluster.status
          ? clusterStatusMap[cluster.status]
          : 'TERMINATED',
        cluster: cluster.name,
        user: user_name,
        user_hash: cluster.user_hash,
        cluster_hash: cluster.cluster_hash,
        cloud: cloud,
        region: '',
        infra: cloud,
        full_infra: cloud,
        resources_str: cluster.resources_str,
        resources_str_full: cluster.resources_str_full,
        time: cluster.launched_at ? new Date(cluster.launched_at * 1000) : null,
        num_nodes: cluster.num_nodes || 1,
        duration: cluster.duration,
        total_cost: cluster.total_cost,
        workspace: cluster.workspace || 'default',
        autostop: -1,
        last_event: cluster.last_event,
        to_down: false,
        cluster_name_on_cloud: null,
        node_names: cluster.node_names || null,
        usage_intervals: cluster.usage_intervals,
        command: cluster.last_creation_command || '',
        task_yaml: cluster.last_creation_yaml || '{}',
        events: [
          {
            time: cluster.launched_at
              ? new Date(cluster.launched_at * 1000)
              : new Date(),
            event: 'Cluster created.',
          },
        ],
      };
    });

    // Apply plugin data enhancements
    // Pass raw backend data so enhancements can extract fields directly
    const enhancedHistory = await applyEnhancements(historyData, 'clusters', {
      dashboardCache,
      rawData: history, // Raw backend response for field extraction
    });
    return enhancedHistory;
  } catch (error) {
    console.error('Error fetching cluster history:', error);
    throw error;
  }
}

export async function streamClusterJobLogs({
  clusterName,
  jobId,
  onNewLog,
  workspace,
  signal,
}) {
  try {
    await apiClient.stream(
      '/logs',
      {
        follow: false,
        cluster_name: clusterName,
        job_id: jobId,
        tail: DEFAULT_TAIL_LINES,
        override_skypilot_config: {
          active_workspace: workspace || 'default',
        },
      },
      onNewLog,
      { signal }
    );
  } catch (error) {
    // Abort is an expected control path (e.g., user refresh/navigation).
    if (error?.name === 'AbortError') {
      return;
    }
    console.error('Error in streamClusterJobLogs:', error);
    showToast(`Error in streamClusterJobLogs: ${error.message}`, 'error');
  }
}

/**
 * Downloads job logs as a zip via the API server.
 * Flow:
 * 1) POST /download_logs to fetch logs from the remote cluster to API server
 * 2) POST /download to stream a zip back to the browser and trigger download
 */
export async function downloadJobLogs({
  clusterName,
  jobIds = null,
  workspace,
}) {
  try {
    // Step 1: schedule server-side download; result is a mapping job_id -> folder path on API server
    const mapping = await apiClient.fetch('/download_logs', {
      cluster_name: clusterName,
      job_ids: jobIds ? jobIds.map(String) : null, // Convert to strings as expected by server
      override_skypilot_config: {
        active_workspace: workspace || 'default',
      },
    });

    const folderPaths = Object.values(mapping || {});
    if (!folderPaths.length) {
      showToast('No logs found to download.', 'warning');
      return;
    }

    // Step 2: request the zip and trigger browser download
    const baseUrl = window.location.origin;
    const fullUrl = `${baseUrl}${ENDPOINT}/download`;
    const resp = await fetch(`${fullUrl}?relative=items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_paths: folderPaths }),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Download failed: ${resp.status} ${text}`);
    }
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    const ts = new Date().toISOString().replace(/[:.]/g, '-');
    const namePart =
      jobIds && jobIds.length === 1 ? `job-${jobIds[0]}` : 'jobs';
    a.href = url;
    a.download = `${clusterName}-${namePart}-logs-${ts}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (error) {
    console.error('Error downloading logs:', error);
    showToast(`Error downloading logs: ${error.message}`, 'error');
  }
}

export async function getClusterJobs({ clusterName, workspace }) {
  try {
    const jobs = await apiClient.fetch('/queue', {
      cluster_name: clusterName,
      all_users: true,
      override_skypilot_config: {
        active_workspace: workspace,
      },
    });

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
        user_hash: job.user_hash,
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
        workspace: workspace || 'default',
        git_commit: job.metadata?.git_commit || '-',
      };
    });
    return jobData;
  } catch (error) {
    console.error('Error fetching cluster jobs:', error);
    throw error;
  }
}

export function useClusterDetails({ cluster, job = null }) {
  const [clusterData, setClusterData] = useState(null);
  const [clusterJobData, setClusterJobData] = useState(null);
  const [loadingClusterData, setLoadingClusterData] = useState(true);
  const [loadingClusterJobData, setLoadingClusterJobData] = useState(true);

  // Separate loading states - cluster details vs cluster jobs
  const clusterDetailsLoading = loadingClusterData;
  const clusterJobsLoading = loadingClusterJobData;

  const fetchClusterData = useCallback(async () => {
    if (cluster) {
      try {
        setLoadingClusterData(true);
        // Use dashboard cache for cluster data
        const data = await dashboardCache.get(getClusters, [
          { clusterNames: [cluster] },
        ]);
        if (data.length > 0) {
          setClusterData(data[0]); // Assuming getClusters returns an array
          return data[0]; // Return the data for use in fetchClusterJobData
        } else {
          console.error('No cluster data found for cluster:', cluster);
          return null;
        }
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
          // Use dashboard cache for cluster jobs
          const data = await dashboardCache.get(getClusterJobs, [
            {
              clusterName: cluster,
              workspace: workspace || 'default',
            },
          ]);
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
    // Invalidate cache for fresh data
    dashboardCache.invalidate(getClusters, [{ clusterNames: [cluster] }]);

    const clusterInfo = await fetchClusterData();
    if (clusterInfo) {
      // Invalidate cluster jobs cache for fresh data
      dashboardCache.invalidate(getClusterJobs, [
        {
          clusterName: cluster,
          workspace: clusterInfo.workspace || 'default',
        },
      ]);
      await fetchClusterJobData(clusterInfo.workspace);
    }
  }, [fetchClusterData, fetchClusterJobData, cluster]);

  const refreshClusterJobsOnly = useCallback(async () => {
    if (clusterData) {
      // Invalidate only cluster jobs cache for fresh data
      dashboardCache.invalidate(getClusterJobs, [
        {
          clusterName: cluster,
          workspace: clusterData.workspace || 'default',
        },
      ]);
      await fetchClusterJobData(clusterData.workspace);
    }
  }, [fetchClusterJobData, clusterData, cluster]);

  useEffect(() => {
    const initializeData = async () => {
      const clusterInfo = await fetchClusterData();
      if (clusterInfo) {
        // Start loading cluster jobs independently
        fetchClusterJobData(clusterInfo.workspace);
      }
    };
    initializeData();
  }, [cluster, job, fetchClusterData, fetchClusterJobData]);

  return {
    clusterData,
    clusterJobData,
    loading: clusterDetailsLoading, // Only cluster details loading for initial page render
    clusterDetailsLoading,
    clusterJobsLoading,
    refreshData,
    refreshClusterJobsOnly,
  };
}

// ============ useClusterData Hook ============

/**
 * Hook for cluster data with pagination support.
 * If the pagination plugin is available, uses server-side pagination.
 * Otherwise, falls back to client-side pagination with getClusters.
 *
 * With requires_early_init=True, the plugin is guaranteed to be loaded
 * before the first API call completes, so we just need a simple check.
 *
 * @param {Object} options - Hook options
 * @param {boolean} options.showHistory - Whether to include historical clusters
 * @param {number} options.historyDays - Number of days of history to fetch
 * @param {number} options.refreshInterval - Auto-refresh interval in ms
 * @returns {Object} Cluster data with pagination state and actions
 */
export function useClusterData(options = {}) {
  const {
    showHistory = false,
    historyDays = 1,
    refreshInterval = null,
    sortConfig = { key: null, direction: 'ascending' },
    filters = [],
  } = options;

  // Convert sortConfig to API format
  // Default to launched_at desc (newest first) when no sort is specified
  const sortBy = sortConfig.key || 'launched_at';
  const sortOrder = sortConfig.key
    ? sortConfig.direction === 'ascending'
      ? 'asc'
      : 'desc'
    : 'desc'; // Default to desc when no sort key selected

  // Serialize filters for stable dependency comparison
  const filtersKey = JSON.stringify(filters);

  const [data, setData] = useState([]);
  const [fullData, setFullData] = useState([]); // Full dataset for client-side filtering
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(10);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [hasNext, setHasNext] = useState(false);
  const [hasPrev, setHasPrev] = useState(false);
  const [error, setError] = useState(null);
  const [isServerPagination, setIsServerPagination] = useState(false);

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [filtersKey]);

  /**
   * Fetch clusters using server-side pagination (plugin path)
   */
  const fetchServerSide = useCallback(async () => {
    console.log('[useClusterData] Using server-side pagination');
    const pluginFetch = getPaginationFetch();

    const result = await dashboardCache.get(pluginFetch, [
      {
        page,
        limit,
        showHistory,
        historyDays,
        sortBy,
        sortOrder,
        filters,
      },
    ]);

    const resultTotal = result.total || 0;
    const resultTotalPages = result.totalPages || result.total_pages || 1;
    const resultHasNext = result.hasNext || result.has_next || false;
    const resultHasPrev = result.hasPrev || result.has_prev || false;
    const resultData = result.items || result.data || [];

    setData(resultData);
    setFullData(resultData);
    setTotal(resultTotal);
    setTotalPages(resultTotalPages);
    setHasNext(resultHasNext);
    setHasPrev(resultHasPrev);
    setIsServerPagination(true);

    // Prefetch next page in background if there is one
    if (resultHasNext) {
      const nextPageOptions = {
        page: page + 1,
        limit,
        showHistory,
        historyDays,
        sortBy,
        sortOrder,
        filters,
      };
      dashboardCache
        .get(pluginFetch, [nextPageOptions], { ttl: 30000 })
        .then(() => console.log('[useClusterData] Prefetched page', page + 1))
        .catch((err) => console.warn('[useClusterData] Prefetch failed:', err));
    }
  }, [page, limit, showHistory, historyDays, sortBy, sortOrder, filters]);

  /**
   * Fetch clusters using client-side pagination (default path)
   */
  const fetchClientSide = useCallback(async () => {
    console.log('[useClusterData] Using client-side pagination');

    const activeClusters = await dashboardCache.get(getClusters);

    let allClusters;
    if (showHistory) {
      let historyClusters = [];
      try {
        historyClusters = await dashboardCache.get(getClusterHistory, [
          null,
          historyDays,
        ]);
      } catch (historyError) {
        console.error('Error fetching cluster history:', historyError);
      }

      const markedActive = activeClusters.map((c) => ({
        ...c,
        isHistorical: false,
      }));
      const markedHistory = historyClusters.map((c) => ({
        ...c,
        isHistorical: true,
      }));

      allClusters = [...markedActive];
      markedHistory.forEach((hist) => {
        if (!activeClusters.some((a) => a.cluster_hash === hist.cluster_hash)) {
          allClusters.push(hist);
        }
      });
    } else {
      allClusters = activeClusters.map((c) => ({
        ...c,
        isHistorical: false,
      }));
    }

    const clientTotal = allClusters.length;
    const clientTotalPages = Math.ceil(clientTotal / limit) || 1;
    const startIndex = (page - 1) * limit;
    const paginatedData = allClusters.slice(startIndex, startIndex + limit);

    setData(paginatedData);
    setFullData(allClusters);
    setTotal(clientTotal);
    setTotalPages(clientTotalPages);
    setHasNext(page < clientTotalPages);
    setHasPrev(page > 1);
    setIsServerPagination(false);
  }, [showHistory, historyDays, page, limit]);

  /**
   * Main fetch function - chooses server or client path
   */
  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      if (isPaginationPluginAvailable()) {
        await fetchServerSide();
      } else {
        await fetchClientSide();
      }
    } catch (fetchError) {
      console.error('[useClusterData] Error fetching clusters:', fetchError);
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
  };
}
