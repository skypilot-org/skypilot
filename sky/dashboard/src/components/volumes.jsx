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
import { RotateCwIcon, Trash2Icon, AlertTriangleIcon } from 'lucide-react';
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
import { useRouter } from 'next/router';
import { TimestampWithTooltip, LastUpdatedTimestamp } from '@/components/utils';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { PluginSlot } from '@/plugins/PluginSlot';
import { usePluginComponents, useTableColumns } from '@/plugins/PluginProvider';
import dashboardCache from '@/lib/cache';
import cachePreloader from '@/lib/cache-preloader';
import { trackVolumeAction } from '@/lib/analytics';

const REFRESH_INTERVAL = REFRESH_INTERVALS.REFRESH_INTERVAL;

export function Volumes() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const refreshDataRef = useRef(null);
  const isMobile = useMobile();
  const [showDeleteConfirmDialog, setShowDeleteConfirmDialog] = useState(false);
  const [volumeToDelete, setVolumeToDelete] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [showPurgeUI, setShowPurgeUI] = useState(false);
  const [purgeConfirmed, setPurgeConfirmed] = useState(false);
  const [purgeLoading, setPurgeLoading] = useState(false);
  const [preloadingComplete, setPreloadingComplete] = useState(false);
  const [lastFetchedTime, setLastFetchedTime] = useState(null);
  const [activeTab, setActiveTab] = useState('volumes');
  const [volumesData, setVolumesData] = useState([]);
  const pluginTabs = usePluginComponents('volumes.tabs');

  const handleTabChange = useCallback(
    (tab) => {
      setActiveTab(tab);
      const query = tab === 'volumes' ? {} : { tab };
      router.replace({ pathname: router.pathname, query }, undefined, {
        shallow: true,
      });
    },
    [router]
  );

  // Sync tab from URL query on mount
  useEffect(() => {
    if (router.isReady && router.query.tab) {
      setActiveTab(router.query.tab);
    }
  }, [router.isReady, router.query.tab]);

  const handleRefresh = () => {
    trackVolumeAction('refresh');
    dashboardCache.invalidate(getVolumes);
    // Reset preloading state so VolumesTable can fetch fresh data immediately
    setPreloadingComplete(false);
    // Trigger a new preload cycle
    cachePreloader.preloadForPage('volumes', { force: true }).then(() => {
      setPreloadingComplete(true);
      setLastFetchedTime(new Date());
      // Call refresh after preloading is complete
      if (refreshDataRef.current) {
        refreshDataRef.current();
      }
    });
  };

  const handleDeleteVolumeClick = (volume) => {
    trackVolumeAction('delete');
    setVolumeToDelete(volume);
    setShowDeleteConfirmDialog(true);
    setDeleteError(null);
    setShowPurgeUI(false);
    setPurgeConfirmed(false);
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
      setShowPurgeUI(false);
      setPurgeConfirmed(false);
      handleRefresh();
    } catch (error) {
      setDeleteError(error);
    } finally {
      setDeleteLoading(false);
    }
  };

  const handlePurgeVolumeConfirm = async () => {
    if (!volumeToDelete) return;

    setPurgeLoading(true);
    setDeleteError(null);

    try {
      const result = await deleteVolume(volumeToDelete.name, { purge: true });
      if (!result.success) {
        throw new Error(result.msg);
      }
      setShowDeleteConfirmDialog(false);
      setVolumeToDelete(null);
      setShowPurgeUI(false);
      setPurgeConfirmed(false);
      handleRefresh();
    } catch (error) {
      setDeleteError(error);
    } finally {
      setPurgeLoading(false);
    }
  };

  const handleCancelDelete = () => {
    setShowDeleteConfirmDialog(false);
    setVolumeToDelete(null);
    setDeleteError(null);
    setShowPurgeUI(false);
    setPurgeConfirmed(false);
  };

  const handleBackFromPurge = () => {
    setShowPurgeUI(false);
    setPurgeConfirmed(false);
  };

  useEffect(() => {
    const preloadData = async () => {
      try {
        // Await cache preloading for volumes page
        await cachePreloader.preloadForPage('volumes');
      } catch (error) {
        console.error('Error preloading volumes data:', error);
      } finally {
        // Signal completion even on error so the table can load
        setPreloadingComplete(true);
        setLastFetchedTime(new Date());
      }
    };
    preloadData();
  }, []);

  const hasPluginTabs = pluginTabs.length > 0;

  return (
    <>
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base flex items-center">
          {hasPluginTabs ? (
            <>
              <button
                className={`leading-none mr-6 pb-2 px-1 border-b-2 ${
                  activeTab === 'volumes'
                    ? 'text-sky-blue border-sky-500'
                    : 'text-gray-500 hover:text-gray-700 border-transparent'
                }`}
                onClick={() => handleTabChange('volumes')}
              >
                Volumes
              </button>
              <PluginSlot
                name="volumes.tabs"
                context={{ activeTab, onTabChange: handleTabChange }}
                wrapperClassName="contents"
              />
            </>
          ) : (
            <Link
              href="/volumes"
              className="text-sky-blue hover:underline leading-none"
            >
              Volumes
            </Link>
          )}
        </div>
        {activeTab === 'volumes' && (
          <div className="flex items-center gap-3">
            {loading && (
              <div className="flex items-center">
                <CircularProgress size={15} className="mt-0" />
                <span className="ml-2 text-gray-500 text-sm">Loading...</span>
              </div>
            )}
            {!loading && lastFetchedTime && (
              <LastUpdatedTimestamp timestamp={lastFetchedTime} />
            )}
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="text-sky-blue hover:text-sky-blue-bright flex items-center"
            >
              <RotateCwIcon className="h-4 w-4 mr-1.5" />
              {!isMobile && <span>Refresh</span>}
            </button>
            <PluginSlot
              name="volumes.header-actions"
              context={{
                onVolumeChange: handleRefresh,
                volumes: volumesData,
              }}
              wrapperClassName="contents"
            />
          </div>
        )}
      </div>

      {activeTab === 'volumes' ? (
        <>
          <VolumesTable
            key="volumes"
            refreshInterval={REFRESH_INTERVAL}
            setLoading={setLoading}
            refreshDataRef={refreshDataRef}
            onDeleteVolume={handleDeleteVolumeClick}
            onDataChange={setVolumesData}
            preloadingComplete={preloadingComplete}
          />

          {/* Delete Confirmation Dialog */}
          <Dialog
            open={showDeleteConfirmDialog}
            onOpenChange={handleCancelDelete}
          >
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>
                  {showPurgeUI ? 'Force remove volume' : 'Delete Volume'}
                </DialogTitle>
                <DialogDescription>
                  {showPurgeUI ? (
                    <>
                      Remove &quot;
                      {volumeToDelete?.name || 'this volume'}&quot; from
                      SkyPilot records. The underlying volume will not be
                      deleted.
                    </>
                  ) : (
                    <>
                      Are you sure you want to delete volume &quot;
                      {volumeToDelete?.name || 'this volume'}&quot;? This action
                      cannot be undone.
                    </>
                  )}
                </DialogDescription>
              </DialogHeader>

              {!showPurgeUI && volumeToDelete?.config?.use_existing && (
                <div className="bg-sky-50 border border-sky-200 rounded-md p-3 my-3 flex items-start gap-2">
                  <AlertTriangleIcon className="w-4 h-4 text-sky-600 mt-0.5 flex-shrink-0" />
                  <div className="text-sm text-sky-900">
                    This volume was imported from an existing{' '}
                    {volumeToDelete?.type === 'k8s-pvc' ? 'PVC' : 'resource'}.
                    Deleting it only removes it from SkyPilot
                    {volumeToDelete?.type === 'k8s-pvc' &&
                    volumeToDelete?.name_on_cloud ? (
                      <>
                        ; the underlying PVC{' '}
                        <code className="bg-sky-100 px-1 rounded">
                          {volumeToDelete.name_on_cloud}
                        </code>
                        {volumeToDelete.namespace &&
                          volumeToDelete.namespace !== '-' && (
                            <>
                              {' '}
                              in namespace{' '}
                              <code className="bg-sky-100 px-1 rounded">
                                {volumeToDelete.namespace}
                              </code>
                            </>
                          )}{' '}
                        will be left intact.
                      </>
                    ) : (
                      <>; the underlying resource will be left intact.</>
                    )}
                  </div>
                </div>
              )}

              {!showPurgeUI && (
                <ErrorDisplay
                  error={deleteError}
                  title="Deletion Failed"
                  onDismiss={() => setDeleteError(null)}
                />
              )}

              {showPurgeUI && (
                <div className="bg-amber-50 border border-amber-200 rounded-md p-3 my-3 flex items-start gap-2">
                  <AlertTriangleIcon className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
                  <div className="text-sm text-amber-800 space-y-2">
                    <p className="m-0">
                      Removing the SkyPilot entry means this volume will no
                      longer appear here, but{' '}
                      {volumeToDelete?.type === 'k8s-pvc' &&
                      volumeToDelete?.name_on_cloud ? (
                        <>
                          the Kubernetes PVC{' '}
                          <code className="bg-amber-100 px-1 rounded">
                            {volumeToDelete.name_on_cloud}
                          </code>
                          {volumeToDelete.namespace &&
                            volumeToDelete.namespace !== '-' && (
                              <>
                                {' '}
                                in namespace{' '}
                                <code className="bg-amber-100 px-1 rounded">
                                  {volumeToDelete.namespace}
                                </code>
                              </>
                            )}{' '}
                          may still exist and continue consuming resources.
                          Delete it manually with{' '}
                          <code className="bg-amber-100 px-1 rounded">
                            kubectl delete pvc
                            {volumeToDelete.namespace &&
                            volumeToDelete.namespace !== '-'
                              ? ` -n ${volumeToDelete.namespace}`
                              : ''}{' '}
                            {volumeToDelete.name_on_cloud}
                          </code>{' '}
                          once it&apos;s no longer in use.
                        </>
                      ) : (
                        <>
                          the underlying cloud resource may still exist and
                          continue consuming resources. Clean it up manually
                          once it&apos;s no longer in use.
                        </>
                      )}
                    </p>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={purgeConfirmed}
                        onChange={(e) => setPurgeConfirmed(e.target.checked)}
                        disabled={purgeLoading}
                        className="cursor-pointer"
                      />
                      <span>
                        I understand force removal may not delete the underlying
                        volume
                      </span>
                    </label>
                  </div>
                </div>
              )}

              <DialogFooter>
                {!showPurgeUI && (
                  <>
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
                      {deleteLoading
                        ? 'Deleting...'
                        : deleteError
                          ? 'Retry Delete'
                          : 'Delete'}
                    </Button>
                    {deleteError && (
                      <Button
                        variant="outline"
                        onClick={() => setShowPurgeUI(true)}
                        disabled={deleteLoading}
                        className="border-amber-600 text-amber-700 hover:bg-amber-50 hover:text-amber-800"
                      >
                        Force remove
                      </Button>
                    )}
                  </>
                )}
                {showPurgeUI && (
                  <>
                    <Button
                      variant="outline"
                      onClick={handleBackFromPurge}
                      disabled={purgeLoading}
                    >
                      Back
                    </Button>
                    <Button
                      onClick={handlePurgeVolumeConfirm}
                      disabled={!purgeConfirmed || purgeLoading}
                      className="bg-amber-600 hover:bg-amber-700 text-white"
                    >
                      {purgeLoading ? 'Removing...' : 'Force Remove'}
                    </Button>
                  </>
                )}
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      ) : (
        <PluginSlot
          name="volumes.tab-content"
          context={{ activeTab, onTabChange: handleTabChange }}
        />
      )}
    </>
  );
}

function VolumesTable({
  refreshInterval,
  setLoading,
  refreshDataRef,
  onDeleteVolume,
  onDataChange,
  preloadingComplete,
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
      if (onDataChange) {
        onDataChange(volumesData);
      }
    } catch (error) {
      console.error('Failed to fetch volumes:', error);
      setData([]);
      if (onDataChange) {
        onDataChange([]);
      }
    } finally {
      setLoading(false);
      setLocalLoading(false);
      setIsInitialLoad(false);
    }
  }, [setLoading, onDataChange]);

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

    // Only start fetching data after preloading is complete
    if (preloadingComplete) {
      fetchData();

      const interval = setInterval(() => {
        if (isCurrent && window.document.visibilityState === 'visible') {
          fetchData();
        }
      }, refreshInterval);

      return () => {
        isCurrent = false;
        clearInterval(interval);
      };
    }

    return () => {
      isCurrent = false;
    };
  }, [refreshInterval, fetchData, preloadingComplete]);

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
    if (size == null) return '-';
    if (size >= 1024) return `${+(size / 1024).toFixed(1)}Ti`;
    return `${size}Gi`;
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

  const pluginColumns = useTableColumns('volumes');

  const sortableHeader = (label, sortKey) => (
    <TableHead
      className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
      onClick={() => requestSort(sortKey)}
    >
      {label}
      {getSortDirection(sortKey)}
    </TableHead>
  );

  const baseColumns = [
    {
      id: 'name',
      order: 0,
      renderHeader: () => sortableHeader('Name', 'name'),
      renderCell: (volume) => (
        <TableCell>
          <Link
            href={`/volumes/${encodeURIComponent(volume.name)}`}
            className="text-blue-600"
          >
            {volume.name}
          </Link>
        </TableCell>
      ),
    },
    {
      id: 'infra',
      order: 10,
      renderHeader: () => sortableHeader('Infra', 'infra'),
      renderCell: (volume) => <TableCell>{volume.infra || 'N/A'}</TableCell>,
    },
    {
      id: 'status',
      order: 20,
      renderHeader: () => sortableHeader('Status', 'status'),
      renderCell: (volume) => (
        <TableCell>
          <StatusBadge
            status={volume.status}
            statusTooltip={volume.error_message || volume.status}
          />
        </TableCell>
      ),
    },
    {
      id: 'size',
      order: 30,
      renderHeader: () => sortableHeader('Size', 'size'),
      renderCell: (volume) => <TableCell>{formatSize(volume.size)}</TableCell>,
    },
    {
      id: 'user_name',
      order: 40,
      renderHeader: () => sortableHeader('User', 'user_name'),
      renderCell: (volume) => (
        <TableCell>{volume.user_name || 'N/A'}</TableCell>
      ),
    },
    {
      id: 'last_attached_at',
      order: 50,
      renderHeader: () => sortableHeader('Last Use', 'last_attached_at'),
      renderCell: (volume) => (
        <TableCell>{formatTimestamp(volume.last_attached_at)}</TableCell>
      ),
    },
    {
      id: 'type',
      order: 60,
      renderHeader: () => sortableHeader('Type', 'type'),
      renderCell: (volume) => <TableCell>{volume.type || 'N/A'}</TableCell>,
    },
    {
      id: 'usedby_clusters',
      order: 70,
      renderHeader: () => sortableHeader('Used By', 'usedby_clusters'),
      renderCell: (volume) => (
        <TableCell>
          <UsedByCell
            clusters={volume.usedby_clusters}
            pods={volume.usedby_pods}
          />
        </TableCell>
      ),
    },
    {
      id: 'actions',
      order: 1000,
      renderHeader: () => <TableHead>Actions</TableHead>,
      renderCell: (volume) => (
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
      ),
    },
  ];

  const pluginColumnDefs = pluginColumns.map((col) => ({
    id: col.id,
    order: col.header.order,
    isPlugin: true,
    renderHeader: () => {
      const baseClasses = col.header.sortKey
        ? 'sortable whitespace-nowrap cursor-pointer hover:bg-gray-50'
        : 'whitespace-nowrap';
      const className = `${baseClasses}${col.header.className ? ' ' + col.header.className : ''}`;
      return (
        <TableHead
          className={className}
          onClick={
            col.header.sortKey
              ? () => requestSort(col.header.sortKey)
              : undefined
          }
        >
          {col.header.label}
          {col.header.sortKey ? getSortDirection(col.header.sortKey) : ''}
        </TableHead>
      );
    },
    renderCell: (volume) => {
      const cellContent = col.cell.render(volume, { item: volume });
      return (
        <TableCell className={col.cell.className || ''}>
          {cellContent}
        </TableCell>
      );
    },
  }));

  const visibleColumns = [...baseColumns, ...pluginColumnDefs].sort(
    (a, b) => a.order - b.order
  );
  const totalColSpan = visibleColumns.length;

  return (
    <div>
      <Card>
        <div className="overflow-x-auto rounded-lg">
          <Table className="min-w-full">
            <TableHeader>
              <TableRow>
                {visibleColumns.map((col) =>
                  React.cloneElement(col.renderHeader(), { key: col.id })
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading || !preloadingComplete ? (
                <TableRow>
                  <TableCell
                    colSpan={totalColSpan}
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
                    {visibleColumns.map((col) =>
                      React.cloneElement(col.renderCell(volume), {
                        key: col.id,
                      })
                    )}
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell
                    colSpan={totalColSpan}
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
  onDataChange: PropTypes.func,
  preloadingComplete: PropTypes.bool.isRequired,
};
