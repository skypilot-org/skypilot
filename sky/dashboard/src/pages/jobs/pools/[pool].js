import React, { useState, useEffect, useCallback } from 'react';
import Head from 'next/head';
import { useRouter } from 'next/router';
import Link from 'next/link';
import { CircularProgress } from '@mui/material';
// Using simple HTML table to avoid component issues
import { formatDuration, REFRESH_INTERVAL } from '@/components/utils';
import { getQueryPools, extractHandleInfo } from '@/data/connectors/jobs';

import { TimestampWithTooltip } from '@/components/utils';
import { RefreshCw } from 'lucide-react';
import { StatusBadge } from '@/components/elements/StatusBadge';
import dashboardCache from '@/lib/cache';

export default function PoolDetailPage() {
  const router = useRouter();
  const { pool: poolName } = router.query;
  const [poolData, setPoolData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchPoolData = useCallback(async () => {
    if (!poolName) return;

    setLoading(true);
    try {
      const data = await dashboardCache.get(getQueryPools);

      // Find the specific pool
      let pool = null;
      if (data && data.pools && Array.isArray(data.pools)) {
        pool = data.pools.find((p) => p.name === poolName);
      } else if (data && Array.isArray(data)) {
        pool = data.find((p) => p.name === poolName);
      }

      if (!pool) {
        setError(`Pool ${poolName} not found`);
        setPoolData(null);
      } else {
        setPoolData(pool);
        setError(null);
      }
    } catch (err) {
      setError('Failed to fetch pool data');
      setPoolData(null);
    } finally {
      setLoading(false);
    }
  }, [poolName]);

  useEffect(() => {
    fetchPoolData();
  }, [poolName, fetchPoolData]);

  const formatUptime = (uptime) => {
    if (!uptime) return '-';

    const now = Date.now() / 1000;
    const durationSeconds = Math.floor(now - uptime);

    if (durationSeconds <= 0) return '-';

    return formatDuration(durationSeconds);
  };

  const getWorkersCount = (replicaInfo) => {
    if (!replicaInfo || replicaInfo.length === 0) return '0';

    const readyWorkers = replicaInfo.filter(
      (worker) => worker.status === 'READY'
    ).length;

    return readyWorkers.toString();
  };

  // Helper function to get infrastructure summary for ready workers
  const getInfraSummary = (replicaInfo) => {
    if (!replicaInfo || replicaInfo.length === 0) {
      return {};
    }

    const infraCounts = {};
    replicaInfo.forEach((worker) => {
      if (worker.handle && worker.status === 'READY') {
        const handleInfo = extractHandleInfo(worker.handle);
        const cloud = handleInfo.cloud;
        infraCounts[cloud] = (infraCounts[cloud] || 0) + 1;
      }
    });

    return infraCounts;
  };

  if (loading) {
    return (
      <>
        <Head>
          <title>Pool {poolName} | SkyPilot Dashboard</title>
        </Head>
        <div className="min-h-screen flex items-center justify-center">
          <div className="flex flex-col items-center">
            <CircularProgress size={32} className="mb-3" />
            <span className="text-gray-600">Loading pool details...</span>
          </div>
        </div>
      </>
    );
  }

  if (error || !poolData) {
    return (
      <>
        <Head>
          <title>Pool {poolName} | SkyPilot Dashboard</title>
        </Head>
        <div className="bg-white shadow rounded-lg p-6">
          <div className="text-center text-red-600">
            <h2 className="text-xl font-semibold mb-2">Error</h2>
            <p>{error || `Pool ${poolName} not found`}</p>
            <button
              onClick={fetchPoolData}
              className="mt-4 bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600"
            >
              Retry
            </button>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <Head>
        <title>Pool {poolName} | SkyPilot Dashboard</title>
      </Head>
      <div>
        {/* Header */}
        <div className="w-full mb-4">
          <div className="flex items-center justify-between mb-4 h-5">
            <div className="text-base flex items-center">
              <Link href="/jobs" className="text-sky-blue hover:underline">
                Pools
              </Link>
              <span className="mx-2 text-gray-500">›</span>
              <Link
                href={`/jobs/pools/${poolName}`}
                className="text-sky-blue hover:underline"
              >
                {poolName}
              </Link>
            </div>

            <div className="text-sm flex items-center">
              {loading && (
                <div className="flex items-center mr-4">
                  <CircularProgress size={15} className="mt-0" />
                  <span className="ml-2 text-gray-500">Loading...</span>
                </div>
              )}
              <button
                onClick={fetchPoolData}
                disabled={loading}
                className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center"
              >
                <RefreshCw
                  className={`w-4 h-4 mr-1.5 ${loading ? 'animate-spin' : ''}`}
                />
                <span>Refresh</span>
              </button>
            </div>
          </div>
        </div>

        {/* Content sections - explicitly stacked vertically */}
        <div className="w-full flex flex-col space-y-6">
          {/* Pool Summary */}
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-xl font-semibold mb-4">Pool Summary</h2>
            <div className="grid grid-cols-2 gap-x-8 gap-y-2">
              {/* Row 1: Job Status | Infra Summary */}
              <div>
                <div className="text-sm font-medium text-gray-700 mb-2">
                  Job Status
                </div>
                <div className="flex flex-wrap gap-1">
                  {poolData.jobCounts &&
                  Object.keys(poolData.jobCounts).length > 0 ? (
                    Object.entries(poolData.jobCounts).map(
                      ([status, count]) => {
                        const getStatusStyle = (status) => {
                          switch (status) {
                            case 'PENDING':
                              return 'bg-yellow-50 text-yellow-700';
                            case 'STARTING':
                              return 'bg-cyan-50 text-cyan-700';
                            case 'RUNNING':
                              return 'bg-green-50 text-green-700';
                            case 'RECOVERING':
                              return 'bg-orange-50 text-orange-700';
                            case 'CANCELLING':
                              return 'bg-yellow-50 text-yellow-700';
                            default:
                              return 'bg-gray-50 text-gray-700';
                          }
                        };

                        return (
                          <span
                            key={status}
                            className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${getStatusStyle(status)}`}
                          >
                            {count} {status}
                          </span>
                        );
                      }
                    )
                  ) : (
                    <span className="text-gray-500 text-sm">
                      No active jobs
                    </span>
                  )}
                </div>
              </div>

              <div>
                <div className="text-sm font-medium text-gray-700 mb-2">
                  Infra Summary
                </div>
                <div className="flex flex-wrap gap-1">
                  {(() => {
                    const infraCounts = getInfraSummary(poolData.replica_info);
                    return Object.keys(infraCounts).length > 0 ? (
                      Object.entries(infraCounts).map(([cloud, count]) => (
                        <span
                          key={cloud}
                          className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-blue-50 text-blue-700"
                        >
                          {count} {cloud}
                        </span>
                      ))
                    ) : (
                      <span className="text-gray-500 text-sm">-</span>
                    );
                  })()}
                </div>
              </div>
            </div>

            {/* Horizontal separator */}
            <div className="pt-2 border-t mt-2">
              <div className="grid grid-cols-2 gap-x-8 gap-y-2">
                {/* Row 2: Workers | Policy */}
                <div>
                  <div className="text-sm font-medium text-gray-700 mb-1">
                    Workers
                  </div>
                  <div className="text-lg font-mono">
                    {getWorkersCount(poolData.replica_info)}
                  </div>
                </div>

                <div>
                  <div className="text-sm font-medium text-gray-700 mb-1">
                    Policy
                  </div>
                  <div className="text-sm">{poolData.policy || '-'}</div>
                </div>

                {/* Row 3: Status | Uptime */}
                <div>
                  <div className="text-sm font-medium text-gray-700 mb-1">
                    Status
                  </div>
                  <StatusBadge status={poolData.status} />
                </div>

                <div>
                  <div className="text-sm font-medium text-gray-700 mb-1">
                    Uptime
                  </div>
                  <div className="text-lg font-mono">
                    {formatUptime(poolData.uptime)}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Workers Table */}
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-xl font-semibold mb-4">Pool Workers</h2>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      ID
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Version
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Launched
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Infrastructure
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Resources
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Status
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Used By
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {poolData.replica_info && poolData.replica_info.length > 0 ? (
                    poolData.replica_info.map((worker, index) => (
                      <tr key={worker.replica_id}>
                        <td className="px-6 py-4 whitespace-nowrap text-sm font-mono">
                          {worker.replica_id}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                          {worker.name}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                          <TimestampWithTooltip
                            date={new Date(worker.launched_at * 1000)}
                          />
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 max-w-xs truncate">
                          {(() => {
                            try {
                              if (!worker.handle) {
                                return 'No handle';
                              }

                              // Extract infrastructure info from base64-encoded handle
                              const handleInfo = extractHandleInfo(
                                worker.handle
                              );
                              return handleInfo.cloud;
                            } catch (error) {
                              return `Error: ${error.message}`;
                            }
                          })()}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 max-w-xs truncate">
                          {(() => {
                            try {
                              if (!worker.handle) {
                                return 'No handle';
                              }

                              // Extract resource info from base64-encoded handle
                              const handleInfo = extractHandleInfo(
                                worker.handle
                              );
                              return handleInfo.instanceType;
                            } catch (error) {
                              return `Error: ${error.message}`;
                            }
                          })()}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                          <StatusBadge status={worker.status} />
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                          {worker.used_by || '-'}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td
                        colSpan={7}
                        className="px-6 py-8 text-center text-gray-500"
                      >
                        No workers found in this pool
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
