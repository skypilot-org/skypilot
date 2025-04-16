/**
 * This code was generated by v0 by Vercel.
 * @see https://v0.dev/t/X5tLGA3WPNU
 * Documentation: https://v0.dev/docs#integrating-generated-code-into-your-nextjs-app
 */
import React, { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import { CircularProgress } from '@mui/material';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { formatDuration } from '@/components/utils';
import { getManagedJobs } from '@/data/connectors/jobs';
import { getClusters } from '@/data/connectors/clusters';
import { Layout } from '@/components/elements/layout';
import { CustomTooltip as Tooltip, relativeTime } from '@/components/utils';
import {
  FileSearchIcon,
  RotateCwIcon,
  MonitorPlay,
  RefreshCcw,
} from 'lucide-react';
import { handleJobAction } from '@/data/connectors/jobs';
import { ConfirmationModal } from '@/components/elements/modals';
import { isJobController } from '@/data/utils';
import { StatusBadge, getStatusStyle } from '@/components/elements/StatusBadge';

export function ManagedJobs() {
  const [loading, setLoading] = useState(false);
  const refreshDataRef = React.useRef(null);
  const [confirmationModal, setConfirmationModal] = useState({
    isOpen: false,
    title: '',
    message: '',
    onConfirm: null,
  });

  const handleRefresh = () => {
    if (refreshDataRef.current) {
      refreshDataRef.current();
    }
  };

  return (
    <Layout highlighted="jobs">
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base">
          <Link
            href="/jobs"
            className="text-sky-blue hover:underline leading-none"
          >
            Managed Jobs
          </Link>
        </div>
        <div className="flex items-center space-x-2">
          {loading && (
            <div className="flex items-center mr-2">
              <CircularProgress size={15} className="mt-0" />
              <span className="ml-2 text-gray-500 text-sm">Loading...</span>
            </div>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleRefresh}
            disabled={loading}
            className="text-sky-blue hover:text-sky-blue-bright"
            title="Refresh"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            <span>Refresh</span>
          </Button>
        </div>
      </div>
      <ManagedJobsTable
        refreshInterval={20000}
        setLoading={setLoading}
        refreshDataRef={refreshDataRef}
      />
      <ConfirmationModal
        isOpen={confirmationModal.isOpen}
        onClose={() =>
          setConfirmationModal({ ...confirmationModal, isOpen: false })
        }
        onConfirm={confirmationModal.onConfirm}
        title={confirmationModal.title}
        message={confirmationModal.message}
      />
    </Layout>
  );
}

export function ManagedJobsTable({
  refreshInterval,
  setLoading,
  refreshDataRef,
}) {
  const [data, setData] = useState([]);
  const [sortConfig, setSortConfig] = useState({
    key: null,
    direction: 'ascending',
  });
  const [loading, setLocalLoading] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [expandedRowId, setExpandedRowId] = useState(null);
  const expandedRowRef = useRef(null);
  const [selectedStatuses, setSelectedStatuses] = useState([]);
  const [statusCounts, setStatusCounts] = useState({});
  const [controllerStopped, setControllerStopped] = useState(false);
  const [controllerLaunching, setControllerLaunching] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);
  const [activeTab, setActiveTab] = useState('active');
  const [showAllMode, setShowAllMode] = useState(true);
  const [confirmationModal, setConfirmationModal] = useState({
    isOpen: false,
    title: '',
    message: '',
    onConfirm: null,
  });

  const handleRestartController = async () => {
    setConfirmationModal({
      isOpen: true,
      title: 'Restart Controller',
      message:
        'Are you sure you want to restart the controller? This will temporarily interrupt job management.',
      onConfirm: async () => {
        try {
          setIsRestarting(true);
          setLocalLoading(true);
          await handleJobAction('restartcontroller', null, null, true);
          // Refresh data after restarting the controller
          await fetchData();
        } catch (err) {
          console.error('Error restarting controller:', err);
        } finally {
          setIsRestarting(false);
          setLocalLoading(false);
        }
      },
    });
  };

  const fetchData = React.useCallback(async () => {
    setLocalLoading(true);
    setLoading(true); // Set parent loading state
    try {
      // Fetch both jobs and clusters data in parallel
      const [jobsResponse, clustersData] = await Promise.all([
        getManagedJobs(),
        getClusters(),
      ]);

      const { jobs, controllerStopped } = jobsResponse;
      // for the clusters, check if there is a cluster that `isJobController`
      const jobControllerCluster = clustersData.find((c) =>
        isJobController(c.cluster)
      );
      const jobControllerClusterStatus = jobControllerCluster
        ? jobControllerCluster.status
        : 'NOT_FOUND';
      let isControllerStopped = false;
      if (jobControllerClusterStatus == 'STOPPED' && controllerStopped) {
        isControllerStopped = true;
      }
      if (jobControllerClusterStatus == 'LAUNCHING') {
        setControllerLaunching(true);
      } else {
        setControllerLaunching(false);
      }

      setData(jobs);
      setControllerStopped(isControllerStopped);
    } catch (err) {
      console.error('Error fetching data:', err);
      setData([]);
    } finally {
      setLocalLoading(false);
      setLoading(false); // Clear parent loading state
      setIsInitialLoad(false);
    }
  }, [setLoading]);

  // Expose fetchData to parent component
  React.useEffect(() => {
    if (refreshDataRef) {
      refreshDataRef.current = fetchData;
    }
  }, [refreshDataRef, fetchData]);

  useEffect(() => {
    setData([]);
    let isCurrent = true;

    fetchData();

    const interval = setInterval(() => {
      if (isCurrent) {
        fetchData();
      }
    }, refreshInterval);

    return () => {
      isCurrent = false;
      clearInterval(interval);
    };
  }, [refreshInterval, fetchData]);

  // Reset to first page when activeTab changes or when data changes
  useEffect(() => {
    setCurrentPage(1);
  }, [activeTab, data.length]);

  // Reset status filter when activeTab changes
  useEffect(() => {
    setSelectedStatuses([]);
    setShowAllMode(true); // Default to show all mode when changing tabs
  }, [activeTab]);

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

  // Define status groups
  const statusGroups = {
    active: [
      'PENDING',
      'RUNNING',
      'RECOVERING',
      'SUBMITTED',
      'STARTING',
      'CANCELLING',
    ],
    finished: [
      'SUCCEEDED',
      'FAILED',
      'CANCELLED',
      'FAILED_SETUP',
      'FAILED_PRECHECKS',
      'FAILED_NO_RESOURCE',
      'FAILED_CONTROLLER',
    ],
  };

  // Calculate active and finished counts
  const counts = React.useMemo(() => {
    const active = data.filter((item) =>
      statusGroups.active.includes(item.status)
    ).length;
    const finished = data.filter((item) =>
      statusGroups.finished.includes(item.status)
    ).length;
    return { active, finished };
  }, [data]);

  // Helper function to determine if a status should be highlighted
  const isStatusHighlighted = (status) => {
    // If we have selected statuses, highlight only those statuses
    if (selectedStatuses.length > 0) {
      return selectedStatuses.includes(status);
    }

    // If no statuses are selected, highlight all statuses in the active tab
    return statusGroups[activeTab].includes(status);
  };

  // Filter data based on selected statuses and active tab
  const filteredData = React.useMemo(() => {
    // If specific statuses are selected, show jobs with any of those statuses
    if (selectedStatuses.length > 0) {
      return data.filter((item) => selectedStatuses.includes(item.status));
    }

    // If no statuses are selected but we're in "show all" mode, show all jobs of the active tab
    if (showAllMode) {
      return data.filter((item) =>
        statusGroups[activeTab].includes(item.status)
      );
    }

    // If no statuses are selected and we're not in "show all" mode, show no jobs
    return [];
  }, [data, activeTab, selectedStatuses, showAllMode]);

  // Handle status selection
  const handleStatusClick = (status) => {
    // Toggle the clicked status without affecting others
    if (selectedStatuses.includes(status)) {
      // If the status is already selected, unselect it
      const newSelectedStatuses = selectedStatuses.filter((s) => s !== status);

      if (newSelectedStatuses.length === 0) {
        // When deselecting the last selected status, go back to "show all" mode
        // for the current active tab (active/finished)
        setShowAllMode(true);
        setSelectedStatuses([]);
      } else {
        setSelectedStatuses(newSelectedStatuses);
        // We're not in "show all" mode if there are specific statuses selected
        setShowAllMode(false);
      }
    } else {
      // Add the clicked status to the selected statuses
      setSelectedStatuses([...selectedStatuses, status]);
      // We're not in "show all" mode if there are specific statuses selected
      setShowAllMode(false);
    }
  };

  // Update status counts when data changes
  useEffect(() => {
    const counts = data.reduce((acc, job) => {
      acc[job.status] = (acc[job.status] || 0) + 1;
      return acc;
    }, {});
    setStatusCounts(counts);
  }, [data]);

  // Calculate pagination
  const totalPages = Math.ceil(filteredData.length / pageSize);
  const startIndex = (currentPage - 1) * pageSize;
  const endIndex = startIndex + pageSize;

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
    setCurrentPage(1); // Reset to first page when changing page size
  };

  const paginatedData = filteredData.slice(startIndex, endIndex);

  return (
    <div className="relative">
      <div className="flex flex-col space-y-1 mb-1">
        {/* Combined Status Filter */}
        <div className="flex flex-wrap items-center text-sm mb-1">
          <span className="mr-2 text-sm font-medium">Statuses:</span>
          <div className="flex flex-wrap gap-2 items-center">
            {!loading && data.length === 0 && !isInitialLoad && (
              <span className="text-gray-500 mr-2">No jobs found</span>
            )}
            {Object.entries(statusCounts).map(([status, count]) => (
              <button
                key={status}
                onClick={() => handleStatusClick(status)}
                className={`px-3 py-1 rounded-full flex items-center space-x-2 ${
                  isStatusHighlighted(status) ||
                  selectedStatuses.includes(status)
                    ? getBadgeStyle(status)
                    : 'bg-gray-50 text-gray-600 hover:bg-gray-100'
                }`}
              >
                <span>{status}</span>
                <span
                  className={`text-xs ${isStatusHighlighted(status) || selectedStatuses.includes(status) ? 'bg-white/50' : 'bg-gray-200'} px-1.5 py-0.5 rounded`}
                >
                  {count}
                </span>
              </button>
            ))}
            {data.length > 0 && (
              <div className="flex items-center ml-2 gap-2">
                <span className="text-gray-500">(</span>
                <button
                  onClick={() => {
                    // When showing all active jobs, clear all selected statuses
                    setActiveTab('active');
                    setSelectedStatuses([]);
                    setShowAllMode(true);
                  }}
                  className={`text-sm font-medium ${
                    activeTab === 'active' && showAllMode
                      ? 'text-green-700 underline'
                      : 'text-gray-600 hover:text-green-700 hover:underline'
                  }`}
                >
                  show all active jobs
                </button>
                <span className="text-gray-500 mx-1">|</span>
                <button
                  onClick={() => {
                    // When showing all finished jobs, clear all selected statuses
                    setActiveTab('finished');
                    setSelectedStatuses([]);
                    setShowAllMode(true);
                  }}
                  className={`text-sm font-medium ${
                    activeTab === 'finished' && showAllMode
                      ? 'text-blue-700 underline'
                      : 'text-gray-600 hover:text-blue-700 hover:underline'
                  }`}
                >
                  show all finished jobs
                </button>
                <span className="text-gray-500">)</span>
              </div>
            )}
          </div>
        </div>
      </div>

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('id')}
              >
                ID{getSortDirection('id')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('name')}
              >
                Name{getSortDirection('name')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('user')}
              >
                User{getSortDirection('user')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('submitted_at')}
              >
                Submitted{getSortDirection('submitted_at')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('job_duration')}
              >
                Duration{getSortDirection('job_duration')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('status')}
              >
                Status{getSortDirection('status')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('resources')}
              >
                Resources{getSortDirection('resources')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('cluster')}
              >
                Cluster{getSortDirection('cluster')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('region')}
              >
                Region{getSortDirection('region')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('recoveries')}
              >
                Recoveries{getSortDirection('recoveries')}
              </TableHead>
              <TableHead>Details</TableHead>
              <TableHead>Logs</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading && isInitialLoad ? (
              <TableRow>
                <TableCell
                  colSpan={12}
                  className="text-center py-6 text-gray-500"
                >
                  <div className="flex justify-center items-center">
                    <CircularProgress size={20} className="mr-2" />
                    <span>Loading...</span>
                  </div>
                </TableCell>
              </TableRow>
            ) : paginatedData.length > 0 ? (
              <>
                {paginatedData.map((item) => (
                  <React.Fragment key={item.id}>
                    <TableRow>
                      <TableCell>
                        <Link
                          href={`/jobs/${item.id}`}
                          className="text-blue-600"
                        >
                          {item.id}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <Link
                          href={`/jobs/${item.id}`}
                          className="text-blue-600"
                        >
                          {item.name}
                        </Link>
                      </TableCell>
                      <TableCell>{item.user}</TableCell>
                      <TableCell>{relativeTime(item.submitted_at)}</TableCell>
                      <TableCell>{formatDuration(item.job_duration)}</TableCell>
                      <TableCell>
                        <StatusBadge status={item.status} />
                      </TableCell>
                      <TableCell>{item.resources}</TableCell>
                      <TableCell>{item.cluster}</TableCell>
                      <TableCell>{item.region}</TableCell>
                      <TableCell>{item.recoveries}</TableCell>
                      <TableCell>
                        {item.details ? (
                          <TruncatedDetails
                            text={item.details}
                            rowId={item.id}
                            expandedRowId={expandedRowId}
                            setExpandedRowId={setExpandedRowId}
                          />
                        ) : (
                          '-'
                        )}
                      </TableCell>
                      <TableCell>
                        <Status2Actions
                          jobParent="/jobs"
                          jobId={item.id}
                          managed={true}
                        />
                      </TableCell>
                    </TableRow>
                    {expandedRowId === item.id && (
                      <ExpandedDetailsRow
                        text={item.details}
                        colSpan={12}
                        innerRef={expandedRowRef}
                      />
                    )}
                  </React.Fragment>
                ))}
              </>
            ) : (
              <TableRow>
                <TableCell colSpan={12} className="text-center py-6">
                  <div className="flex flex-col items-center space-y-4">
                    {controllerLaunching && (
                      <div className="flex flex-col items-center space-y-2">
                        <p className="text-gray-700">
                          The managed job controller is launching. Please wait
                          for it to be ready.
                        </p>
                        <div className="flex items-center">
                          <CircularProgress size={12} className="mr-2" />
                          <span className="text-gray-500">Launching...</span>
                        </div>
                      </div>
                    )}
                    {!controllerStopped && !controllerLaunching && (
                      <p className="text-gray-500">No active jobs</p>
                    )}
                    {controllerStopped && (
                      <div className="flex flex-col items-center space-y-2">
                        <p className="text-gray-700">
                          The managed job controller has been stopped. Please
                          restart it to check the latest job status.
                        </p>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={handleRestartController}
                          className="text-sky-blue hover:text-sky-blue-bright"
                          disabled={loading || isRestarting}
                        >
                          {isRestarting ? (
                            <>
                              <CircularProgress size={12} className="mr-2" />
                              Restarting...
                            </>
                          ) : (
                            <>
                              <RefreshCcw className="h-4 w-4 mr-2" />
                              Restart Controller
                            </>
                          )}
                        </Button>
                      </div>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>

      {/* Pagination controls */}
      {filteredData.length > 0 && (
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
                  <option value={10}>10</option>
                  <option value={30}>30</option>
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                  <option value={200}>200</option>
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
              {startIndex + 1} – {Math.min(endIndex, filteredData.length)} of{' '}
              {filteredData.length}
            </div>
            <div className="flex items-center space-x-2">
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
                disabled={currentPage === totalPages || totalPages === 0}
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
        </div>
      )}

      <ConfirmationModal
        isOpen={confirmationModal.isOpen}
        onClose={() =>
          setConfirmationModal({ ...confirmationModal, isOpen: false })
        }
        onConfirm={confirmationModal.onConfirm}
        title={confirmationModal.title}
        message={confirmationModal.message}
      />
    </div>
  );
}

// Helper function to get status-specific styling
function getBadgeStyle(status) {
  return getStatusStyle(status);
}

export function Status2Actions({
  withLabel = false,
  jobParent,
  jobId,
  managed,
}) {
  const router = useRouter();

  const handleLogsClick = (e, type) => {
    e.preventDefault();
    e.stopPropagation();
    router.push({
      pathname: `${jobParent}/${jobId}`,
      query: { tab: type },
    });
  };

  return (
    <div className="flex items-center space-x-4">
      <Tooltip
        key="logs"
        content="View Job Logs"
        className="capitalize text-sm text-muted-foreground"
      >
        <button
          onClick={(e) => handleLogsClick(e, 'logs')}
          className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center h-8"
        >
          <FileSearchIcon className="w-4 h-4" />
          {withLabel && <span className="ml-1.5">Logs</span>}
        </button>
      </Tooltip>
      {managed && (
        <Tooltip
          key="controllerlogs"
          content="View Controller Logs"
          className="capitalize text-sm text-muted-foreground"
        >
          <button
            onClick={(e) => handleLogsClick(e, 'controllerlogs')}
            className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center h-8"
          >
            <MonitorPlay className="w-4 h-4" />
            {withLabel && <span className="ml-2">Controller Logs</span>}
          </button>
        </Tooltip>
      )}
    </div>
  );
}

export function ClusterJobs({ clusterName, clusterJobData, loading }) {
  const [expandedRowId, setExpandedRowId] = useState(null);
  const [sortConfig, setSortConfig] = useState({
    key: null,
    direction: 'ascending',
  });
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const expandedRowRef = useRef(null);
  const [prevClusterJobData, setPrevClusterJobData] = useState(null);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (
        expandedRowId &&
        expandedRowRef.current &&
        !expandedRowRef.current.contains(event.target)
      ) {
        setExpandedRowId(null);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [expandedRowId]);

  const jobData = React.useMemo(() => {
    return clusterJobData || [];
  }, [clusterJobData]);

  useEffect(() => {
    // Check if the data has changed significantly (new data received)
    if (JSON.stringify(clusterJobData) !== JSON.stringify(prevClusterJobData)) {
      setPrevClusterJobData(clusterJobData);
    }
  }, [clusterJobData, prevClusterJobData]);

  // Sort the data if sortConfig is present
  const sortedData = React.useMemo(() => {
    if (!sortConfig.key) return jobData;

    return [...jobData].sort((a, b) => {
      if (a[sortConfig.key] < b[sortConfig.key]) {
        return sortConfig.direction === 'ascending' ? -1 : 1;
      }
      if (a[sortConfig.key] > b[sortConfig.key]) {
        return sortConfig.direction === 'ascending' ? 1 : -1;
      }
      return 0;
    });
  }, [jobData, sortConfig]);

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

  // Calculate pagination
  const totalPages = Math.ceil(sortedData.length / pageSize);
  const startIndex = (currentPage - 1) * pageSize;
  const endIndex = startIndex + pageSize;
  const paginatedData = sortedData.slice(startIndex, endIndex);

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
    setCurrentPage(1); // Reset to first page when changing page size
  };

  return (
    <div className="relative">
      <Card>
        <div className="flex items-center justify-between p-4">
          <h3 className="text-lg font-semibold">Cluster Jobs</h3>
          {loading && (
            <div className="flex items-center mr-2">
              <CircularProgress size={15} className="mt-0" />
              <span className="ml-2 text-gray-500 text-sm">Loading...</span>
            </div>
          )}
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('id')}
              >
                ID{getSortDirection('id')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('job')}
              >
                Name{getSortDirection('job')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('user')}
              >
                User{getSortDirection('user')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('submitted_at')}
              >
                Submitted{getSortDirection('submitted_at')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('job_duration')}
              >
                Duration{getSortDirection('job_duration')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('status')}
              >
                Status{getSortDirection('status')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('resources')}
              >
                Resources{getSortDirection('resources')}
              </TableHead>
              <TableHead className="whitespace-nowrap">Logs</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedData.length > 0 ? (
              paginatedData.map((item) => (
                <React.Fragment key={item.id}>
                  <TableRow
                    className={expandedRowId === item.id ? 'selected-row' : ''}
                  >
                    <TableCell>
                      <Link
                        href={`/clusters/${clusterName}/${item.id}`}
                        className="text-blue-600"
                      >
                        {item.id}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/clusters/${clusterName}/${item.id}`}
                        className="text-blue-600"
                      >
                        <TruncatedDetails
                          text={item.job || 'Unnamed job'}
                          rowId={item.id}
                          expandedRowId={expandedRowId}
                          setExpandedRowId={setExpandedRowId}
                        />
                      </Link>
                    </TableCell>
                    <TableCell>{item.user}</TableCell>
                    <TableCell>{relativeTime(item.submitted_at)}</TableCell>
                    <TableCell>{formatDuration(item.job_duration)}</TableCell>
                    <TableCell>
                      <StatusBadge status={item.status} />
                    </TableCell>
                    <TableCell>{item.resources}</TableCell>
                    <TableCell className="flex content-center items-center">
                      <Status2Actions
                        jobParent={`/clusters/${clusterName}`}
                        jobId={item.id}
                        managed={false}
                      />
                    </TableCell>
                  </TableRow>
                  {expandedRowId === item.id && (
                    <ExpandedDetailsRow
                      text={item.job || 'Unnamed job'}
                      colSpan={8}
                      innerRef={expandedRowRef}
                    />
                  )}
                </React.Fragment>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={8}
                  className="text-center py-6 text-gray-500"
                >
                  No jobs found
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>

      {sortedData.length > 0 && (
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
              {startIndex + 1} – {Math.min(endIndex, sortedData.length)} of{' '}
              {sortedData.length}
            </div>
            <div className="flex items-center space-x-2">
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
                disabled={currentPage === totalPages || totalPages === 0}
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
        </div>
      )}
    </div>
  );
}

function ExpandedDetailsRow({ text, colSpan, innerRef }) {
  return (
    <TableRow className="expanded-details">
      <TableCell colSpan={colSpan}>
        <div
          className="p-4 bg-gray-50 rounded-md border border-gray-200"
          ref={innerRef}
        >
          <div className="flex justify-between items-start">
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-900">Full Details</p>
              <p
                className="mt-1 text-sm text-gray-700"
                style={{ whiteSpace: 'pre-wrap' }}
              >
                {text}
              </p>
            </div>
          </div>
        </div>
      </TableCell>
    </TableRow>
  );
}

function TruncatedDetails({ text, rowId, expandedRowId, setExpandedRowId }) {
  const isTruncated = text.length > 50;
  const isExpanded = expandedRowId === rowId;
  // Always show truncated text in the table cell
  const displayText = isTruncated ? `${text.substring(0, 50)}` : text;
  const buttonRef = useRef(null);

  const handleClick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setExpandedRowId(isExpanded ? null : rowId);
  };

  return (
    <div className="truncated-details relative max-w-full flex items-center">
      <span className="truncate">{displayText}</span>
      {isTruncated && (
        <button
          ref={buttonRef}
          type="button"
          onClick={handleClick}
          className="text-blue-600 hover:text-blue-800 font-medium ml-1 flex-shrink-0"
          data-button-type="show-more-less"
        >
          {isExpanded ? '... show less' : '... show more'}
        </button>
      )}
    </div>
  );
}
