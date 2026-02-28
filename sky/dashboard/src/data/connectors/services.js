'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiClient } from '@/data/connectors/client';
import { getErrorMessageFromResponse } from '@/data/utils';
import dashboardCache from '@/lib/cache';

/**
 * Fetch all SkyServe services via the /serve/status endpoint.
 * Uses the two-phase request-ID pattern via apiClient.fetch().
 */
export async function getServices() {
  try {
    const data = await apiClient.fetch('/serve/status', {
      service_names: null,
    });
    // data is an array of service status dicts
    return (data || []).map((svc) => ({
      name: svc.name,
      status: svc.status,
      uptime: svc.uptime,
      endpoint: svc.endpoint,
      controller_job_id: svc.controller_job_id,
      active_versions: svc.active_versions,
      policy: svc.policy,
      requested_resources_str: svc.requested_resources_str,
      load_balancing_policy: svc.load_balancing_policy,
      tls_encrypted: svc.tls_encrypted,
      replica_info: svc.replica_info || [],
    }));
  } catch (error) {
    console.error('Error fetching services:', error);
    throw error;
  }
}

/**
 * Delete (tear down) a service.
 */
export async function deleteService(serviceName) {
  try {
    const response = await apiClient.post('/serve/down', {
      service_names: [serviceName],
      all: false,
      purge: false,
    });
    if (!response.ok) {
      return {
        success: false,
        msg: `Failed to delete service: status ${response.status}`,
      };
    }
    const id = response.headers.get('X-Skypilot-Request-ID');
    if (!id) {
      return { success: false, msg: 'No request ID received from server' };
    }
    const fetchedData = await apiClient.get(`/api/get?request_id=${id}`);
    if (!fetchedData.ok) {
      const errorMessage = await getErrorMessageFromResponse(fetchedData);
      return {
        success: false,
        msg: `Failed to delete service with status ${fetchedData.status}, error: ${errorMessage}`,
      };
    }
    return { success: true };
  } catch (error) {
    return { success: false, msg: `Failed to delete service: ${error}` };
  }
}

/**
 * Stream service logs via the /serve/logs endpoint.
 */
export async function streamServiceLogs({
  serviceName,
  target = 'REPLICA',
  replicaId = null,
  onNewLog,
  signal,
}) {
  const body = {
    service_name: serviceName,
    target: target,
    follow: false,
  };
  if (replicaId !== null) {
    body.replica_id = replicaId;
  }
  // Use fetchImmediate (like job logs) to inject env_vars for auth
  const response = await apiClient.fetchImmediate(
    '/serve/logs',
    body,
    'POST',
    { signal }
  );
  if (!response.ok) {
    throw new Error(
      `Failed to fetch service logs: status ${response.status}`
    );
  }
  const reader = response.body.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = new TextDecoder().decode(value);
      onNewLog(chunk);
    }
  } finally {
    if (!signal || !signal.aborted) {
      try {
        reader.cancel();
      } catch (_) {
        // ignore
      }
    }
  }
}

/**
 * Hook: fetch all services with caching.
 */
export function useServices(refreshTrigger = 0) {
  const [services, setServices] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const data = await dashboardCache.get(getServices);
      setServices(data || []);
    } catch (error) {
      console.error('Error fetching services:', error);
      setServices([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData, refreshTrigger]);

  const refresh = useCallback(() => {
    dashboardCache.invalidate(getServices);
    fetchData();
  }, [fetchData]);

  return { services, loading, refresh };
}

/**
 * Hook: fetch a single service detail.
 */
export function useServiceDetail(serviceName, refreshTrigger = 0) {
  const [serviceData, setServiceData] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    if (!serviceName) return;
    setLoading(true);
    try {
      const data = await dashboardCache.get(getServices);
      const svc = (data || []).find((s) => s.name === serviceName);
      setServiceData(svc || null);
    } catch (error) {
      console.error('Error fetching service detail:', error);
      setServiceData(null);
    } finally {
      setLoading(false);
    }
  }, [serviceName]);

  useEffect(() => {
    fetchData();
  }, [fetchData, refreshTrigger]);

  const refresh = useCallback(() => {
    dashboardCache.invalidate(getServices);
    fetchData();
  }, [fetchData]);

  return { serviceData, loading, refresh };
}
