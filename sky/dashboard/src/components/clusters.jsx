/**
 * This code was generated by v0 by Vercel.
 * @see https://v0.dev/t/t5SMh01qKCm
 * Documentation: https://v0.dev/docs#integrating-generated-code-into-your-nextjs-app
 */
'use client';

import React, { useState, useEffect } from 'react';
import { CircularProgress } from '@mui/material';
import { CustomTooltip as Tooltip } from '@/components/utils';
import {
  FilledCircleIcon,
  SquareIcon,
  PauseIcon,
} from '@/components/elements/icons';
import Link from 'next/link';
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
import { getClusters } from '@/data/connectors/clusters';
import { sortData, isController } from '@/data/utils';
import { SquareCode, Terminal, PlayIcon, RotateCwIcon } from 'lucide-react';
import { relativeTime } from '@/components/utils';
import { Layout } from '@/components/elements/layout';
import {
  SSHInstructionsModal,
  VSCodeInstructionsModal,
  ConfirmationModal,
} from '@/components/elements/modals';

export function Clusters() {
  const [activeTab, setActiveTab] = useState('active');
  const [loading, setLoading] = useState(false);
  const refreshDataRef = React.useRef(null);
  const [isSSHModalOpen, setIsSSHModalOpen] = useState(false);
  const [isVSCodeModalOpen, setIsVSCodeModalOpen] = useState(false);
  const [selectedCluster, setSelectedCluster] = useState(null);

  const handleRefresh = () => {
    if (refreshDataRef.current) {
      refreshDataRef.current();
    }
  };

  return (
    <Layout highlighted="clusters">
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base">
          <Link href="/clusters" className="text-sky-blue leading-none">
            Sky Clusters
          </Link>
        </div>
        <div className="flex items-center">
          {loading && (
            <div className="flex items-center mr-2">
              <CircularProgress size={15} className="mt-0" />
              <span className="ml-2 text-gray-500">Loading...</span>
            </div>
          )}
          <Button
            variant="ghost"
            onClick={handleRefresh}
            disabled={loading}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            <span>Refresh</span>
          </Button>
        </div>
      </div>
      <ClusterTable
        activeTab={activeTab}
        refreshInterval={10000}
        setLoading={setLoading}
        refreshDataRef={refreshDataRef}
        onOpenSSHModal={(cluster) => {
          setSelectedCluster(cluster);
          setIsSSHModalOpen(true);
        }}
        onOpenVSCodeModal={(cluster) => {
          setSelectedCluster(cluster);
          setIsVSCodeModalOpen(true);
        }}
      />

      {/* SSH Instructions Modal */}
      <SSHInstructionsModal
        isOpen={isSSHModalOpen}
        onClose={() => setIsSSHModalOpen(false)}
        cluster={selectedCluster}
      />

      <VSCodeInstructionsModal
        isOpen={isVSCodeModalOpen}
        onClose={() => setIsVSCodeModalOpen(false)}
        cluster={selectedCluster}
      />
    </Layout>
  );
}

export function ClusterTable({
  activeTab,
  refreshInterval,
  setLoading,
  refreshDataRef,
  onOpenSSHModal,
  onOpenVSCodeModal,
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

  const fetchData = React.useCallback(async () => {
    setLoading(true);
    setLocalLoading(true);
    const initialData = await getClusters();
    setData(initialData);
    setLoading(false);
    setLocalLoading(false);
    setIsInitialLoad(false);
  }, [activeTab, setLoading]);

  // Use useMemo to compute sorted data
  const sortedData = React.useMemo(() => {
    return sortData(data, sortConfig.key, sortConfig.direction);
  }, [data, sortConfig]);

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
  }, [activeTab, refreshInterval, fetchData]);

  // Reset to first page when activeTab changes or when data changes
  useEffect(() => {
    setCurrentPage(1);
  }, [activeTab, data.length]);

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

  // Calculate pagination using sortedData
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
    <div>
      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('status')}
              >
                Status{getSortDirection('status')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('cluster')}
              >
                Cluster{getSortDirection('cluster')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('user')}
              >
                User{getSortDirection('user')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('resources_str')}
              >
                Resources{getSortDirection('resources_str')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('region')}
              >
                Region{getSortDirection('region')}
              </TableHead>
              <TableHead
                className="sortable whitespace-nowrap"
                onClick={() => requestSort('time')}
              >
                Started{getSortDirection('time')}
              </TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>

          <TableBody>
            {loading && isInitialLoad ? (
              <TableRow>
                <TableCell
                  colSpan={8}
                  className="text-center py-6 text-gray-500"
                >
                  <div className="flex justify-center items-center">
                    <CircularProgress size={20} className="mr-2" />
                    <span>Loading...</span>
                  </div>
                </TableCell>
              </TableRow>
            ) : paginatedData.length > 0 ? (
              paginatedData.map((item, index) => {
                return (
                  <TableRow key={index}>
                    <TableCell>
                      <Status2Icon status={item.status} />
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/clusters/${item.cluster}`}
                        className="text-blue-600"
                      >
                        {item.cluster}
                      </Link>
                    </TableCell>
                    <TableCell>{item.user}</TableCell>
                    <TableCell>{item.resources_str}</TableCell>
                    <TableCell>{item.region}</TableCell>
                    <TableCell>{relativeTime(item.time)}</TableCell>
                    <TableCell className="text-left">
                      <Status2Actions
                        cluster={item.cluster}
                        status={item.status}
                        onOpenSSHModal={onOpenSSHModal}
                        onOpenVSCodeModal={onOpenVSCodeModal}
                      />
                    </TableCell>
                  </TableRow>
                );
              })
            ) : (
              <TableRow>
                <TableCell
                  colSpan={8}
                  className="text-center py-6 text-gray-500"
                >
                  No active clusters
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>

      {/* Pagination controls */}
      {data.length > 0 && (
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
              {startIndex + 1} – {Math.min(endIndex, data.length)} of{' '}
              {data.length}
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

function status2Icon(status) {
  const badgeClasses = 'inline-flex items-center px-2 py-1 rounded-full';
  switch (status) {
    case 'LAUNCHING':
      return (
        <span className={`${badgeClasses} bg-blue-100 text-sky-blue`}>
          <CircularProgress size={12} className="w-3 h-3 mr-1" />
          LAUNCHING
        </span>
      );
    case 'RUNNING':
      return (
        <span className={`${badgeClasses} bg-green-100 text-green-800`}>
          <FilledCircleIcon className="w-3 h-3 mr-1" />
          RUNNING
        </span>
      );
    case 'STOPPED':
      return (
        <span className={`${badgeClasses} bg-yellow-100 text-yellow-800`}>
          <PauseIcon className="w-3 h-3 mr-1" />
          STOPPED
        </span>
      );
    case 'TERMINATED':
      return (
        <span className={`${badgeClasses} bg-gray-100 text-gray-800`}>
          <SquareIcon className="w-3 h-3 mr-1" />
          TERMINATED
        </span>
      );
    default:
      return (
        <span className={`${badgeClasses} bg-gray-100 text-gray-800`}>
          <FilledCircleIcon className="w-3 h-3 mr-1" />
          {status}
        </span>
      );
  }
}

export function Status2Icon({ status }) {
  return (
    <Tooltip content={status}>
      <span>{status2Icon(status)}</span>
    </Tooltip>
  );
}

export const handleVSCodeConnection = (cluster, onOpenVSCodeModal) => {
  if (onOpenVSCodeModal) {
    onOpenVSCodeModal(cluster);
  }
};

const handleConnect = (cluster, onOpenSSHModal) => {
  if (onOpenSSHModal) {
    onOpenSSHModal(cluster);
  } else {
    const uri = `ssh://${cluster}`;
    window.open(uri);
  }
};

// TODO(hailong): The enabled actions are also related to the `cloud` of the cluster
export const enabledActions = (status, cluster) => {
  switch (status) {
    case 'RUNNING':
      return ['connect', 'VSCode'];
    default:
      return [];
  }
};

const actionIcons = {
  connect: <Terminal className="w-5 h-5 text-gray-500 inline-block" />,
  VSCode: <SquareCode className="w-5 h-5 text-gray-500 inline-block" />,
};

export function Status2Actions({
  withLabel = false,
  cluster,
  status,
  onOpenSSHModal,
  onOpenVSCodeModal,
}) {
  const [confirmationModal, setConfirmationModal] = React.useState({
    isOpen: false,
    title: '',
    message: '',
    onConfirm: null,
  });

  const actions = enabledActions(status, cluster);

  const handleActionClick = (actionName) => {
    switch (actionName) {
      case 'connect':
        handleConnect(cluster, onOpenSSHModal);
        break;
      case 'VSCode':
        handleVSCodeConnection(cluster, onOpenVSCodeModal);
        break;
      default:
        return;
    }
  };

  return (
    <>
      <div className="flex items-center space-x-4">
        {Object.entries(actionIcons).map(([actionName, actionIcon]) => {
          let label, tooltipText;
          switch (actionName) {
            case 'connect':
              label = 'Connect';
              tooltipText = 'Connect with SSH';
              break;
            case 'VSCode':
              label = 'VSCode';
              tooltipText = 'Open in VS Code';
              break;
            default:
              break;
          }
          if (!withLabel) {
            label = '';
          }
          if (actions.includes(actionName)) {
            return (
              <Tooltip
                key={actionName}
                content={tooltipText}
                className="capitalize text-sm text-muted-foreground"
              >
                <button
                  onClick={() => handleActionClick(actionName)}
                  className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center"
                >
                  {actionIcon}
                  {label && <span className="ml-1.5">{label}</span>}
                </button>
              </Tooltip>
            );
          }
          return (
            <Tooltip
              key={actionName}
              content={tooltipText}
              className="capitalize text-sm text-muted-foreground"
            >
              <span
                className="opacity-30 flex items-center cursor-not-allowed text-sm"
                title={actionName}
              >
                {actionIcon}
                {label && <span className="ml-1.5">{label}</span>}
              </span>
            </Tooltip>
          );
        })}
      </div>
    </>
  );
}
