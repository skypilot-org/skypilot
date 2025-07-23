import React, { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/router';
import Head from 'next/head';
import Link from 'next/link';
import { CircularProgress } from '@mui/material';
import {
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
} from 'lucide-react';
import { getQueryPools, extractHandleInfo } from '@/data/connectors/jobs';
import {
  TimestampWithTooltip,
  formatDuration,
  CustomTooltip as Tooltip,
} from '@/components/utils';
import { StatusBadge, getStatusStyle } from '@/components/elements/StatusBadge';
import { formatYaml } from '@/lib/yamlUtils';

export default function PoolDetailPage() {
  const router = useRouter();
  const { pool: poolName } = router.query;

  const [poolData, setPoolData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // Filtering state
  const [showFailedWorkers, setShowFailedWorkers] = useState(false);

  // Pool YAML state
  const [isPoolYamlExpanded, setIsPoolYamlExpanded] = useState(false);
  const [isPoolYamlCopied, setIsPoolYamlCopied] = useState(false);

  const fetchPoolData = async () => {
    if (!poolName) return;

    setLoading(true);
    setError(null);

    try {
      const poolsResponse = await getQueryPools();
      const { pools = [] } = poolsResponse || {};

      const foundPool = pools.find((pool) => pool.name === poolName);
      if (!foundPool) {
        setError(`Pool ${poolName} not found`);
        setPoolData(null);
      } else {
        setPoolData(foundPool);
      }
    } catch (err) {
      console.error('Error fetching pool data:', err);
      setError(`Failed to fetch pool data: ${err.message}`);
      setPoolData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPoolData();
  }, [poolName]);

  // Helper functions
  const getWorkersCount = (replicaInfo) => {
    if (!replicaInfo || replicaInfo.length === 0) return '0';
    const readyWorkers = replicaInfo.filter(
      (worker) => worker.status === 'READY'
    ).length;
    return readyWorkers.toString();
  };

  const getInfraSummary = (replicaInfo) => {
    if (!replicaInfo || replicaInfo.length === 0) return {};

    const readyWorkers = replicaInfo.filter(
      (worker) => worker.status === 'READY'
    );

    const infraCounts = {};
    readyWorkers.forEach((worker) => {
      try {
        const handleInfo = extractHandleInfo(worker.handle);
        const cloud = handleInfo.cloud;
        infraCounts[cloud] = (infraCounts[cloud] || 0) + 1;
      } catch (error) {
        // Handle errors gracefully
        infraCounts['Unknown'] = (infraCounts['Unknown'] || 0) + 1;
      }
    });

    return infraCounts;
  };

  const formatUptime = (uptime) => {
    if (!uptime) return '-';
    const now = Date.now() / 1000;
    const durationSeconds = Math.floor(now - uptime);
    if (durationSeconds <= 0) return '-';
    return formatDuration(durationSeconds);
  };

  const getJobStatusCounts = (poolData) => {
    if (!poolData || !poolData.jobCounts) return {};
    return poolData.jobCounts;
  };

  // Pool YAML functions
  const togglePoolYamlExpanded = () => {
    setIsPoolYamlExpanded(!isPoolYamlExpanded);
  };

  const copyPoolYamlToClipboard = async () => {
    try {
      if (poolData && poolData.pool_yaml) {
        const formattedYaml = formatYaml(poolData.pool_yaml);
        await navigator.clipboard.writeText(formattedYaml);
        setIsPoolYamlCopied(true);
        setTimeout(() => setIsPoolYamlCopied(false), 2000); // Reset after 2 seconds
      }
    } catch (err) {
      console.error('Failed to copy Pool YAML to clipboard:', err);
    }
  };

  // Filter and paginate workers
  const { filteredWorkers, totalPages, paginatedWorkers } = useMemo(() => {
    if (!poolData || !poolData.replica_info) {
      return { filteredWorkers: [], totalPages: 0, paginatedWorkers: [] };
    }

    // Filter workers based on showFailedWorkers toggle
    const filtered = showFailedWorkers
      ? poolData.replica_info
      : poolData.replica_info.filter((worker) => {
          // Check if status contains 'FAILED' (e.g., FAILED_PROBING, FAILED_SETUP, etc.)
          return !worker.status || !worker.status.includes('FAILED');
        });

    // Calculate pagination
    const pages = Math.ceil(filtered.length / pageSize);
    const startIndex = (currentPage - 1) * pageSize;
    const endIndex = startIndex + pageSize;
    const paginated = filtered.slice(startIndex, endIndex);

    return {
      filteredWorkers: filtered,
      totalPages: pages,
      paginatedWorkers: paginated,
    };
  }, [poolData, showFailedWorkers, currentPage, pageSize]);

  // Reset to first page when filtering changes
  useEffect(() => {
    setCurrentPage(1);
  }, [showFailedWorkers]);

  // Page navigation handlers
  const goToPreviousPage = () => {
    setCurrentPage((page) => Math.max(page - 1, 1));
  };

  const goToNextPage = () => {
    setCurrentPage((page) => Math.min(page + 1, totalPages));
  };

  const handlePageSizeChange = (e) => {
    const newSize = parseInt(e.target.value, 10);
    setPageSize(newSize);
    setCurrentPage(1);
  };

  if (!router.isReady || loading) {
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
              <Link
                href="/jobs?tab=pools"
                className="text-sky-blue hover:underline"
              >
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
        <div className="w-full flex flex-col space-y-4">
          {/* Pool Summary */}
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-xl font-semibold mb-4">Pool Summary</h2>

            {/* Grid layout for job status and infra summary */}
            <div className="grid grid-cols-2 gap-x-8 gap-y-2">
              {/* Job Status */}
              <div>
                <div className="text-sm font-medium text-gray-700 mb-2">
                  Job Status
                </div>
                <div className="flex flex-wrap gap-1">
                  {(() => {
                    const jobCounts = getJobStatusCounts(poolData);
                    return Object.keys(jobCounts).length > 0 ? (
                      Object.entries(jobCounts).map(([status, count]) => (
                        <span
                          key={status}
                          className={`px-2 py-1 rounded-full flex items-center space-x-2 text-xs font-medium ${getStatusStyle(status)}`}
                        >
                          <span>{status}</span>
                          <span className="text-xs bg-white/50 px-1.5 py-0.5 rounded">
                            {count}
                          </span>
                        </span>
                      ))
                    ) : (
                      <span className="text-gray-500 text-sm">
                        No active jobs
                      </span>
                    );
                  })()}
                </div>
              </div>

              {/* Infra Summary */}
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

              {/* Placeholder for second column if needed */}
              <div></div>
            </div>

            <div className="pt-2 mt-2">
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
                  <div className="text-lg font-mono">
                    {poolData.policy || '-'}
                  </div>
                </div>

                {/* Row 3: Status */}
                <div>
                  <div className="text-sm font-medium text-gray-700 mb-1">
                    Status
                  </div>
                  <StatusBadge status={poolData.status} />
                </div>
              </div>
            </div>

            {/* Pool YAML Section */}
            {poolData.pool_yaml && poolData.pool_yaml.trim() && (
              <div className="pt-4 mt-4">
                <div className="flex items-center mb-2">
                  <button
                    onClick={togglePoolYamlExpanded}
                    className="flex items-center text-left focus:outline-none text-gray-700 hover:text-gray-900 transition-colors duration-200"
                  >
                    {isPoolYamlExpanded ? (
                      <ChevronDown className="w-4 h-4 mr-1" />
                    ) : (
                      <ChevronRight className="w-4 h-4 mr-1" />
                    )}
                    <span className="text-base">Show Pool YAML</span>
                  </button>

                  <Tooltip
                    content={isPoolYamlCopied ? 'Copied!' : 'Copy YAML'}
                    className="text-muted-foreground"
                  >
                    <button
                      onClick={copyPoolYamlToClipboard}
                      className="flex items-center text-gray-500 hover:text-gray-700 transition-colors duration-200 p-1 ml-2"
                    >
                      {isPoolYamlCopied ? (
                        <Check className="w-4 h-4 text-green-600" />
                      ) : (
                        <Copy className="w-4 h-4" />
                      )}
                    </button>
                  </Tooltip>
                </div>

                {isPoolYamlExpanded && (
                  <div className="bg-gray-50 border border-gray-200 rounded-md p-3 max-h-96 overflow-y-auto">
                    <pre className="text-sm text-gray-800 font-mono whitespace-pre-wrap">
                      {formatYaml(poolData.pool_yaml)}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Workers Table */}
          <div className="bg-white shadow rounded-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold">Pool Workers</h2>

              {/* Show Failed Workers Toggle */}
              <div className="flex items-center space-x-2">
                <label className="flex items-center space-x-3 text-sm cursor-pointer">
                  <div className="relative">
                    <input
                      type="checkbox"
                      checked={showFailedWorkers}
                      onChange={(e) => setShowFailedWorkers(e.target.checked)}
                      className="sr-only"
                    />
                    <div
                      className={`w-11 h-6 rounded-full transition-colors duration-200 ease-in-out ${
                        showFailedWorkers ? 'bg-blue-600' : 'bg-gray-300'
                      }`}
                    >
                      <div
                        className={`w-5 h-5 bg-white rounded-full shadow transform transition-transform duration-200 ease-in-out translate-y-0.5 ${
                          showFailedWorkers
                            ? 'translate-x-5'
                            : 'translate-x-0.5'
                        }`}
                      />
                    </div>
                  </div>
                  <span className="text-gray-700">Show all workers</span>
                </label>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      ID
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
                  {paginatedWorkers.length > 0 ? (
                    paginatedWorkers.map((worker, index) => (
                      <tr key={worker.replica_id}>
                        <td className="px-6 py-4 whitespace-nowrap text-sm font-mono">
                          {worker.replica_id}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                          {worker.launched_at && worker.launched_at > 0 ? (
                            <TimestampWithTooltip
                              date={new Date(worker.launched_at * 1000)}
                            />
                          ) : (
                            '-'
                          )}
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
                        colSpan={6}
                        className="px-6 py-8 text-center text-gray-500"
                      >
                        {showFailedWorkers
                          ? 'No workers found in this pool'
                          : 'No non-failed workers found in this pool'}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {filteredWorkers.length > 0 && (
              <div className="flex justify-end items-center py-2 px-4 text-sm text-gray-700">
                <div className="flex items-center space-x-4">
                  <div className="flex items-center">
                    <span className="mr-2">Rows per page:</span>
                    <div className="relative inline-block">
                      <select
                        value={pageSize}
                        onChange={handlePageSizeChange}
                        className="py-1 pl-2 pr-6 appearance-none outline-none cursor-pointer border-none bg-transparent"
                        style={{ minWidth: '40px' }}
                      >
                        <option value={5}>5</option>
                        <option value={10}>10</option>
                        <option value={20}>20</option>
                        <option value={50}>50</option>
                      </select>
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-4 w-4 text-gray-500 absolute right-0 top-1/2 transform -translate-y-1/2 pointer-events-none"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M19 9l-7 7-7-7"
                        />
                      </svg>
                    </div>
                  </div>
                  <div>
                    {(currentPage - 1) * pageSize + 1} –{' '}
                    {Math.min(currentPage * pageSize, filteredWorkers.length)}{' '}
                    of {filteredWorkers.length}
                  </div>
                  <div className="flex items-center space-x-2">
                    <button
                      onClick={goToPreviousPage}
                      disabled={currentPage === 1}
                      className="px-2 py-1 text-sm text-gray-500 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50 rounded"
                    >
                      &lt;
                    </button>
                    <button
                      onClick={goToNextPage}
                      disabled={currentPage === totalPages || totalPages === 0}
                      className="px-2 py-1 text-sm text-gray-500 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50 rounded"
                    >
                      &gt;
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
