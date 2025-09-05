'use client';

import React, {
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
} from 'react';
import PropTypes from 'prop-types';
import { CircularProgress, Popover } from '@mui/material';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { getVolumes, deleteVolume } from '@/data/connectors/volumes';
import { REFRESH_INTERVALS } from '@/lib/config';
import { sortData } from '@/data/utils';
import { RotateCwIcon, Trash2Icon } from 'lucide-react';
import { useMobile } from '@/hooks/useMobile';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { ErrorDisplay } from '@/components/elements/ErrorDisplay';
import Link from 'next/link';
import { TimestampWithTooltip } from '@/components/utils';
import { StatusBadge } from '@/components/elements/StatusBadge';
import dashboardCache from '@/lib/cache';
import cachePreloader from '@/lib/cache-preloader';

const REFRESH_INTERVAL = REFRESH_INTERVALS.REFRESH_INTERVAL;

export function Volumes() {
  const [loading, setLoading] = useState(false);
  const refreshDataRef = useRef(null);
  const isMobile = useMobile();
  const [showDeleteConfirmDialog, setShowDeleteConfirmDialog] = useState(false);
  const [volumeToDelete, setVolumeToDelete] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  const handleRefresh = () => {
    dashboardCache.invalidate(getVolumes);
    if (refreshDataRef.current) {
      refreshDataRef.current();
    }
  };

  const handleDeleteVolumeClick = (volume) => {
    setVolumeToDelete(volume);
    setShowDeleteConfirmDialog(true);
    setDeleteError(null);
  };

  const handleDeleteVolumeConfirm = async () => {
    if (!volumeToDelete) return;

    setDeleteLoading(true);
    setDeleteError(null);

    try {
      const result = await deleteVolume(volumeToDelete.name);
      if (!result.success) {
        throw new Error(result.msg);
      }
      setShowDeleteConfirmDialog(false);
      setVolumeToDelete(null);
      handleRefresh();
    } catch (error) {
      setDeleteError(error);
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleCancelDelete = () => {
    setShowDeleteConfirmDialog(false);
    setVolumeToDelete(null);
    setDeleteError(null);
  };

  useEffect(() => {
    cachePreloader.preloadForPage('volumes');
  }, []);

  return (
    <>
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base">
          <Link
            href="/volumes"
            className="text-sky-blue hover:underline leading-none"
          >
            Volumes
          </Link>
        </div>
        <div className="flex items-center">
          {loading && (
            <div className="flex items-center mr-2">
              <CircularProgress size={15} className="mt-0" />
              <span className="ml-2 text-gray-500 text-sm">Loading...</span>
            </div>
          )}
          <button
            onClick={handleRefresh}
            disabled={loading}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            {!isMobile && <span>Refresh</span>}
          </button>
        </div>
      </div>

      <VolumesTable
        key="volumes"
        refreshInterval={REFRESH_INTERVAL}
        setLoading={setLoading}
        refreshDataRef={refreshDataRef}
        onDeleteVolume={handleDeleteVolumeClick}
      />

      {/* Delete Confirmation Dialog */}
      <Dialog open={showDeleteConfirmDialog} onOpenChange={handleCancelDelete}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete Volume</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete volume &quot;
              {volumeToDelete?.name || 'this volume'}&quot;? This action cannot
              be undone.
            </DialogDescription>
          </DialogHeader>

          <ErrorDisplay
            error={deleteError}
            title="Deletion Failed"
            onDismiss={() => setDeleteError(null)}
          />

          <DialogFooter>
            <Button
              variant="outline"
              onClick={handleCancelDelete}
              disabled={deleteLoading}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteVolumeConfirm}
              disabled={deleteLoading}
            >
              {deleteLoading ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function VolumesTable({
  refreshInterval,
  setLoading,
  refreshDataRef,
  onDeleteVolume,
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

  const fetchData = useCallback(async () => {
    setLoading(true);
    setLocalLoading(true);
    try {
      const volumesData = await dashboardCache.get(getVolumes);
      setData(volumesData);
    } catch (error) {
      console.error('Failed to fetch volumes:', error);
      setData([]);
    } finally {
      setLoading(false);
      setLocalLoading(false);
      setIsInitialLoad(false);
    }
  }, [setLoading]);

  // Use useMemo to compute sorted data
  const sortedData = useMemo(() => {
    return sortData(data, sortConfig.key, sortConfig.direction);
  }, [data, sortConfig]);

  // Expose fetchData to parent component
  useEffect(() => {
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

  // Reset to first page when data changes
  useEffect(() => {
    setCurrentPage(1);
  }, [data.length]);

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

  const formatSize = (size) => {
    if (!size) return '-';
    return size;
  };

  const formatTimestamp = (timestamp) => {
    if (!timestamp) return 'N/A';
    try {
      const date = new Date(timestamp * 1000); // Convert Unix timestamp to milliseconds
      return <TimestampWithTooltip date={date} />;
    } catch {
      return 'Invalid Date';
    }
  };

  return (
    <div>
      <Card>
        <div className="overflow-x-auto rounded-lg">
          <Table className="min-w-full">
            <TableHeader>
              <TableRow>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('name')}
                >
                  Name{getSortDirection('name')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('infra')}
                >
                  Infra{getSortDirection('infra')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('status')}
                >
                  Status{getSortDirection('status')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('size')}
                >
                  Size{getSortDirection('size')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('user_name')}
                >
                  User{getSortDirection('user_name')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('last_attached_at')}
                >
                  Last Use{getSortDirection('last_attached_at')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('type')}
                >
                  Type{getSortDirection('type')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('usedby_clusters')}
                >
                  Used By{getSortDirection('usedby_clusters')}
                </TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && isInitialLoad ? (
                <TableRow>
                  <TableCell
                    colSpan={11}
                    className="text-center py-6 text-gray-500"
                  >
                    <div className="flex justify-center items-center">
                      <CircularProgress size={20} className="mr-2" />
                      <span>Loading...</span>
                    </div>
                  </TableCell>
                </TableRow>
              ) : paginatedData.length > 0 ? (
                paginatedData.map((volume) => (
                  <TableRow key={volume.name}>
                    <TableCell className="font-medium">{volume.name}</TableCell>
                    <TableCell>{volume.infra || 'N/A'}</TableCell>
                    <TableCell>
                      <StatusBadge status={volume.status} />
                    </TableCell>
                    <TableCell>{formatSize(volume.size)}</TableCell>
                    <TableCell>{volume.user_name || 'N/A'}</TableCell>
                    <TableCell>
                      {formatTimestamp(volume.last_attached_at)}
                    </TableCell>
                    <TableCell>{volume.type || 'N/A'}</TableCell>
                    <TableCell>
                      <UsedByCell
                        clusters={volume.usedby_clusters}
                        pods={volume.usedby_pods}
                      />
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onDeleteVolume(volume)}
                        className="text-red-600 hover:text-red-700 hover:bg-red-50"
                        title="Delete volume"
                      >
                        <Trash2Icon className="w-4 h-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell
                    colSpan={11}
                    className="text-center py-6 text-gray-500"
                  >
                    No volumes found
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
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

function UsedByCell({ clusters, pods }) {
  const MAX_DISPLAY = 2;
  const [anchorEl, setAnchorEl] = useState(null);

  // clusters first
  let arr =
    Array.isArray(clusters) && clusters.length > 0
      ? clusters
      : Array.isArray(pods) && pods.length > 0
        ? pods
        : [];
  const isCluster = Array.isArray(clusters) && clusters.length > 0;

  if (!arr || arr.length === 0) return 'N/A';

  const displayed = arr.slice(0, MAX_DISPLAY);
  const hidden = arr.slice(MAX_DISPLAY);

  const handleClick = (event) => {
    setAnchorEl(event.currentTarget);
  };
  const handleClose = () => {
    setAnchorEl(null);
  };

  return (
    <>
      {displayed.map((item, idx) => (
        <span key={item}>
          {isCluster ? (
            <Link
              href={`/clusters/${encodeURIComponent(item)}`}
              className="text-sky-blue hover:underline"
            >
              {item}
            </Link>
          ) : (
            <span>{item}</span>
          )}
          {idx < displayed.length - 1 ? ', ' : ''}
        </span>
      ))}
      {hidden.length > 0 && (
        <>
          ,{' '}
          <span
            className="text-sky-blue cursor-pointer underline"
            onClick={handleClick}
            style={{ userSelect: 'none' }}
          >
            +{hidden.length} more
          </span>
          <Popover
            open={Boolean(anchorEl)}
            anchorEl={anchorEl}
            onClose={handleClose}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
            transformOrigin={{ vertical: 'top', horizontal: 'left' }}
          >
            <div style={{ padding: 12, maxWidth: 300 }}>
              {hidden.map((item) => (
                <div key={item} style={{ marginBottom: 4 }}>
                  {isCluster ? (
                    <Link
                      href={`/clusters/${encodeURIComponent(item)}`}
                      className="text-sky-blue hover:underline"
                    >
                      {item}
                    </Link>
                  ) : (
                    <span>{item}</span>
                  )}
                </div>
              ))}
            </div>
          </Popover>
        </>
      )}
    </>
  );
}

VolumesTable.propTypes = {
  refreshInterval: PropTypes.number.isRequired,
  setLoading: PropTypes.func.isRequired,
  refreshDataRef: PropTypes.shape({
    current: PropTypes.func,
  }).isRequired,
  onDeleteVolume: PropTypes.func.isRequired,
};
