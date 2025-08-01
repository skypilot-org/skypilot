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
import { getPoolStatus } from '@/data/connectors/jobs';
import dashboardCache from '@/lib/cache';
import {
  getJobStatusCounts,
  getInfraSummary,
  JobStatusBadges,
  InfraBadges,
} from '@/components/utils';
import {
  TimestampWithTooltip,
  formatDuration,
  CustomTooltip as Tooltip,
  NonCapitalizedTooltip,
} from '@/components/utils';
import { UI_CONFIG } from '@/lib/config';
import { StatusBadge, getStatusStyle } from '@/components/elements/StatusBadge';
import { formatYaml } from '@/lib/yamlUtils';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';

export default function PoolDetailPage() {
  const router = useRouter();
  const { pool: poolName } = router.query;

  const [poolData, setPoolData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState(null);

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // Filtering state
  const [showFailedWorkers, setShowFailedWorkers] = useState(false);

  // Sorting state
  const [sortConfig, setSortConfig] = useState({
    key: null,
    direction: 'ascending',
  });

  // Pool YAML state
  const [isPoolYamlExpanded, setIsPoolYamlExpanded] = useState(false);
  const [isPoolYamlCopied, setIsPoolYamlCopied] = useState(false);

  const fetchPoolData = React.useCallback(
    async (isRefresh = false) => {
      if (!poolName) return;

      if (isRefresh) {
        setLoading(true);
      } else {
        setInitialLoading(true);
      }
      setError(null);

      try {
        const poolsResponse = await dashboardCache.get(getPoolStatus, [{}]);
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
        if (isRefresh) {
          setLoading(false);
        } else {
          setInitialLoading(false);
        }
      }
    },
    [poolName, setLoading, setInitialLoading, setError, setPoolData]
  );

  useEffect(() => {
    fetchPoolData();
  }, [poolName, fetchPoolData]);

  // Sorting functions
  const requestSort = (key) => {
    let direction = 'ascending';
    if (sortConfig.key === key && sortConfig.direction === 'ascending') {
      direction = 'descending';
    }
    setSortConfig({ key, direction });
  };

  const getSortDirection = (key) => {
    if (sortConfig.key === key) {
      return sortConfig.direction === 'ascending' ? ' ↑' : ' ↓';
    }
    return '';
  };

  // Helper functions
  const getWorkersCount = (pool) => {
    if (!pool || !pool.replica_info || pool.replica_info.length === 0)
      return '0 (target: 0)';
    const readyWorkers = pool.replica_info.filter(
      (worker) => worker.status === 'READY'
    ).length;
    const targetWorkers = pool.target_num_replicas || 0;
    return `${readyWorkers} (target: ${targetWorkers})`;
  };

  // Using shared getInfraSummary from utils

  const formatUptime = (uptime) => {
    if (!uptime) return '-';
    const now = Date.now() / 1000;
    const durationSeconds = Math.floor(now - uptime);
    if (durationSeconds <= 0) return '-';
    return formatDuration(durationSeconds);
  };

  // Using shared getJobStatusCounts from utils

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
    let filtered = showFailedWorkers
      ? poolData.replica_info
      : poolData.replica_info.filter((worker) => {
          // Check if status contains 'FAILED' (e.g., FAILED_PROBING, FAILED_SETUP, etc.)
          return !worker.status || !worker.status.includes('FAILED');
        });

    // Apply sorting if sortConfig is set
    if (sortConfig.key) {
      filtered = [...filtered].sort((a, b) => {
        let aValue = a[sortConfig.key];
        let bValue = b[sortConfig.key];

        // Handle timestamp sorting
        if (sortConfig.key === 'launched_at') {
          aValue = aValue || 0;
          bValue = bValue || 0;
        }

        // Handle string sorting
        if (typeof aValue === 'string') {
          aValue = aValue.toLowerCase();
        }
        if (typeof bValue === 'string') {
          bValue = bValue.toLowerCase();
        }

        if (aValue < bValue) {
          return sortConfig.direction === 'ascending' ? -1 : 1;
        }
        if (aValue > bValue) {
          return sortConfig.direction === 'ascending' ? 1 : -1;
        }
        return 0;
      });
    }

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
  }, [poolData, showFailedWorkers, currentPage, pageSize, sortConfig]);

  // Reset to first page when filtering or sorting changes
  useEffect(() => {
    setCurrentPage(1);
  }, [showFailedWorkers, sortConfig]);

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

  if (!router.isReady || initialLoading) {
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
              onClick={() => {
                // Invalidate cache to ensure fresh data
                dashboardCache.invalidate(getPoolStatus, [{}]);
                fetchPoolData(true);
              }}
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
              onClick={() => {
                // Invalidate cache to ensure fresh data
                dashboardCache.invalidate(getPoolStatus, [{}]);
                fetchPoolData(true);
              }}
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

        {/* Content sections - explicitly stacked vertically */}
        <div className="w-full flex flex-col space-y-6">
          {/* Details */}
          <div className="mb-6">
            <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
              <div className="flex items-center justify-between px-4 pt-4">
                <h3 className="text-lg font-semibold">Details</h3>
              </div>
              <div className="p-4">
                <div className="grid grid-cols-2 gap-6">
                  {/* Jobs */}
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      Jobs
                    </div>
                    <div className="text-base mt-1">
                      <JobStatusBadges
                        jobCounts={getJobStatusCounts(poolData)}
                        getStatusStyle={getStatusStyle}
                      />
                    </div>
                  </div>

                  {/* Workers */}
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      Workers
                    </div>
                    <div className="text-base mt-1">
                      {getWorkersCount(poolData)}
                    </div>
                  </div>

                  {/* Worker Details */}
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      Worker Details
                    </div>
                    <div className="text-base mt-1">
                      <InfraBadges replicaInfo={poolData.replica_info} />
                    </div>
                  </div>

                  {/* Worker Resources */}
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      Worker Resources
                    </div>
                    <div className="text-base mt-1">
                      {poolData.requested_resources_str || '-'}
                    </div>
                  </div>

                  {/* Policy */}
                  <div>
                    <div className="text-gray-600 font-medium text-base">
                      Policy
                    </div>
                    <div className="text-base mt-1">
                      {poolData.policy || '-'}
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
            </div>
          </div>

          {/* Workers Table */}
          <div className="mb-8">
            <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
              <div className="flex items-center justify-between p-4">
                <h3 className="text-lg font-semibold">Pool Workers</h3>

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

              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead
                      className="sortable whitespace-nowrap"
                      onClick={() => requestSort('replica_id')}
                    >
                      ID{getSortDirection('replica_id')}
                    </TableHead>
                    <TableHead
                      className="sortable whitespace-nowrap"
                      onClick={() => requestSort('launched_at')}
                    >
                      Launched{getSortDirection('launched_at')}
                    </TableHead>
                    <TableHead
                      className="sortable whitespace-nowrap"
                      onClick={() => requestSort('cloud')}
                    >
                      Infra{getSortDirection('cloud')}
                    </TableHead>
                    <TableHead
                      className="sortable whitespace-nowrap"
                      onClick={() => requestSort('resources_str')}
                    >
                      Resources{getSortDirection('resources_str')}
                    </TableHead>
                    <TableHead
                      className="sortable whitespace-nowrap"
                      onClick={() => requestSort('status')}
                    >
                      Status{getSortDirection('status')}
                    </TableHead>
                    <TableHead
                      className="sortable whitespace-nowrap"
                      onClick={() => requestSort('used_by')}
                    >
                      Used By{getSortDirection('used_by')}
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {paginatedWorkers.length > 0 ? (
                    paginatedWorkers.map((worker, index) => (
                      <TableRow key={worker.replica_id}>
                        <TableCell>{worker.replica_id}</TableCell>
                        <TableCell>
                          {worker.launched_at && worker.launched_at > 0 ? (
                            <TimestampWithTooltip
                              date={new Date(worker.launched_at * 1000)}
                            />
                          ) : (
                            '-'
                          )}
                        </TableCell>
                        <TableCell>
                          {(() => {
                            try {
                              const cloudWithRegion = `${worker.cloud} (${worker.region})`;
                              const NAME_TRUNCATE_LENGTH =
                                UI_CONFIG.NAME_TRUNCATE_LENGTH;

                              // Check if there's a region part in parentheses
                              const parenIndex = cloudWithRegion.indexOf('(');
                              if (parenIndex === -1) {
                                // No region part, return as is
                                return cloudWithRegion;
                              }

                              const cloudName = cloudWithRegion
                                .substring(0, parenIndex)
                                .trim();
                              const regionPart = cloudWithRegion.substring(
                                parenIndex + 1,
                                cloudWithRegion.length - 1
                              );

                              // Only truncate the region part if it's longer than the truncate length
                              if (regionPart.length <= NAME_TRUNCATE_LENGTH) {
                                return cloudWithRegion;
                              }

                              // Truncate only the region part
                              const truncatedRegion = `${regionPart.substring(0, Math.floor((NAME_TRUNCATE_LENGTH - 3) / 2))}...${regionPart.substring(regionPart.length - Math.ceil((NAME_TRUNCATE_LENGTH - 3) / 2))}`;
                              const displayText = `${cloudName} (${truncatedRegion})`;

                              return (
                                <NonCapitalizedTooltip
                                  content={cloudWithRegion}
                                  className="text-sm text-muted-foreground"
                                >
                                  <span>{displayText}</span>
                                </NonCapitalizedTooltip>
                              );
                            } catch (error) {
                              return `Error: ${error.message}`;
                            }
                          })()}
                        </TableCell>
                        <TableCell>
                          {(() => {
                            try {
                              return worker.resources_str;
                            } catch (error) {
                              return `Error: ${error.message}`;
                            }
                          })()}
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={worker.status} />
                        </TableCell>
                        <TableCell>
                          {worker.used_by ? (
                            <Link
                              href={`/jobs/${worker.used_by}`}
                              className="text-blue-600 hover:text-blue-800 hover:underline"
                            >
                              Job ID: {worker.used_by}
                            </Link>
                          ) : (
                            '-'
                          )}
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <TableRow>
                      <TableCell
                        colSpan={6}
                        className="text-center py-8 text-gray-500"
                      >
                        {showFailedWorkers
                          ? 'No workers found in this pool'
                          : 'No non-failed workers found in this pool'}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
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
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={goToPreviousPage}
                    disabled={currentPage === 1}
                    className="text-gray-500 h-8 w-8 p-0"
                  >
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      className="chevron-left"
                    >
                      <path d="M15 18l-6-6 6-6" />
                    </svg>
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={goToNextPage}
                    disabled={currentPage === totalPages}
                    className="text-gray-500 h-8 w-8 p-0"
                  >
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      className="chevron-right"
                    >
                      <path d="M9 18l6-6-6-6" />
                    </svg>
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
