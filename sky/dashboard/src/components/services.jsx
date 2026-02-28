'use client';

import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { CircularProgress } from '@mui/material';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { getServices, deleteService } from '@/data/connectors/services';
import { REFRESH_INTERVALS } from '@/lib/config';
import { sortData } from '@/data/utils';
import {
  RotateCwIcon,
  Trash2Icon,
  CopyIcon,
  CheckIcon,
} from 'lucide-react';
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
import { LastUpdatedTimestamp } from '@/components/utils';
import { StatusBadge } from '@/components/elements/StatusBadge';
import dashboardCache from '@/lib/cache';

const REFRESH_INTERVAL = REFRESH_INTERVALS.REFRESH_INTERVAL;

function formatUptime(uptimeTimestamp) {
  if (!uptimeTimestamp || uptimeTimestamp <= 0) return '-';
  // uptime is an absolute Unix timestamp (seconds since epoch)
  const durationSecs = Math.floor(Date.now() / 1000 - uptimeTimestamp);
  if (durationSecs <= 0) return '-';
  const days = Math.floor(durationSecs / 86400);
  const hours = Math.floor((durationSecs % 86400) / 3600);
  const mins = Math.floor((durationSecs % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

export function Services() {
  const [loading, setLoading] = useState(false);
  const refreshDataRef = useRef(null);
  const isMobile = useMobile();
  const [showDeleteConfirmDialog, setShowDeleteConfirmDialog] = useState(false);
  const [serviceToDelete, setServiceToDelete] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [lastFetchedTime, setLastFetchedTime] = useState(null);

  const handleRefresh = () => {
    dashboardCache.invalidate(getServices);
    if (refreshDataRef.current) {
      refreshDataRef.current();
    }
    setLastFetchedTime(new Date());
  };

  const handleDeleteClick = (service) => {
    setServiceToDelete(service);
    setShowDeleteConfirmDialog(true);
    setDeleteError(null);
  };

  const handleDeleteConfirm = async () => {
    if (!serviceToDelete) return;
    setDeleteLoading(true);
    setDeleteError(null);
    try {
      const result = await deleteService(serviceToDelete.name);
      if (!result.success) {
        throw new Error(result.msg);
      }
      setShowDeleteConfirmDialog(false);
      setServiceToDelete(null);
      handleRefresh();
    } catch (error) {
      setDeleteError(error);
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleCancelDelete = () => {
    setShowDeleteConfirmDialog(false);
    setServiceToDelete(null);
    setDeleteError(null);
  };

  return (
    <>
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base">
          <Link
            href="/services"
            className="text-sky-blue hover:underline leading-none"
          >
            Services
          </Link>
        </div>
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
        </div>
      </div>

      <ServicesTable
        refreshInterval={REFRESH_INTERVAL}
        setLoading={setLoading}
        refreshDataRef={refreshDataRef}
        onDeleteService={handleDeleteClick}
        setLastFetchedTime={setLastFetchedTime}
      />

      {/* Delete Confirmation Dialog */}
      <Dialog open={showDeleteConfirmDialog} onOpenChange={handleCancelDelete}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete Service</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete service &quot;
              {serviceToDelete?.name || 'this service'}&quot;? This will tear
              down all replicas. This action cannot be undone.
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
              onClick={handleDeleteConfirm}
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

function ServicesTable({
  refreshInterval,
  setLoading,
  refreshDataRef,
  onDeleteService,
  setLastFetchedTime,
}) {
  const [data, setData] = useState([]);
  const [sortConfig, setSortConfig] = useState({
    key: null,
    direction: 'ascending',
  });
  const [localLoading, setLocalLoading] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [copiedEndpoint, setCopiedEndpoint] = useState(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setLocalLoading(true);
    try {
      const servicesData = await dashboardCache.get(getServices);
      setData(servicesData || []);
    } catch (error) {
      console.error('Failed to fetch services:', error);
      setData([]);
    } finally {
      setLoading(false);
      setLocalLoading(false);
      setIsInitialLoad(false);
      setLastFetchedTime(new Date());
    }
  }, [setLoading, setLastFetchedTime]);

  const sortedData = useMemo(() => {
    return sortData(data, sortConfig.key, sortConfig.direction);
  }, [data, sortConfig]);

  useEffect(() => {
    if (refreshDataRef) {
      refreshDataRef.current = fetchData;
    }
  }, [refreshDataRef, fetchData]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(() => {
      if (window.document.visibilityState === 'visible') {
        fetchData();
      }
    }, refreshInterval);
    return () => clearInterval(interval);
  }, [refreshInterval, fetchData]);

  const requestSort = (key) => {
    let direction = 'ascending';
    if (sortConfig.key === key && sortConfig.direction === 'ascending') {
      direction = 'descending';
    }
    setSortConfig({ key, direction });
  };

  const getSortDirection = (key) => {
    if (sortConfig.key === key) {
      return sortConfig.direction === 'ascending' ? ' \u2191' : ' \u2193';
    }
    return '';
  };

  const copyEndpoint = async (endpoint) => {
    try {
      await navigator.clipboard.writeText(endpoint);
      setCopiedEndpoint(endpoint);
      setTimeout(() => setCopiedEndpoint(null), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  const getReplicaCounts = (service) => {
    const replicas = service.replica_info || [];
    const ready = replicas.filter((r) => r.status === 'READY').length;
    return `${ready}/${replicas.length}`;
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
                  onClick={() => requestSort('status')}
                >
                  Status{getSortDirection('status')}
                </TableHead>
                <TableHead className="whitespace-nowrap">Replicas</TableHead>
                <TableHead className="whitespace-nowrap">Resources</TableHead>
                <TableHead className="whitespace-nowrap">Endpoint</TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('uptime')}
                >
                  Uptime{getSortDirection('uptime')}
                </TableHead>
                <TableHead className="whitespace-nowrap">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {localLoading && isInitialLoad ? (
                <TableRow>
                  <TableCell
                    colSpan={7}
                    className="text-center py-6 text-gray-500"
                  >
                    <div className="flex justify-center items-center">
                      <CircularProgress size={20} className="mr-2" />
                      <span>Loading...</span>
                    </div>
                  </TableCell>
                </TableRow>
              ) : sortedData.length > 0 ? (
                sortedData.map((service) => (
                  <TableRow key={service.name}>
                    <TableCell className="font-medium">
                      <Link
                        href={`/services/${encodeURIComponent(service.name)}`}
                        className="text-sky-blue hover:underline"
                      >
                        {service.name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={service.status} />
                    </TableCell>
                    <TableCell>{getReplicaCounts(service)}</TableCell>
                    <TableCell>
                      <span className="text-sm text-gray-600">
                        {service.requested_resources_str || '-'}
                      </span>
                    </TableCell>
                    <TableCell>
                      {service.endpoint ? (
                        <div className="flex items-center gap-1">
                          <span className="text-sm text-gray-600 truncate max-w-[200px]">
                            {service.endpoint}
                          </span>
                          <button
                            onClick={() => copyEndpoint(service.endpoint)}
                            className="text-gray-400 hover:text-gray-600 p-0.5"
                            title="Copy endpoint"
                          >
                            {copiedEndpoint === service.endpoint ? (
                              <CheckIcon className="w-3.5 h-3.5 text-green-500" />
                            ) : (
                              <CopyIcon className="w-3.5 h-3.5" />
                            )}
                          </button>
                        </div>
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </TableCell>
                    <TableCell>{formatUptime(service.uptime)}</TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onDeleteService(service)}
                        className="text-red-600 hover:text-red-700 hover:bg-red-50"
                        title="Delete service"
                      >
                        <Trash2Icon className="w-4 h-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell
                    colSpan={7}
                    className="text-center py-6 text-gray-500"
                  >
                    No services found. Deploy a service with{' '}
                    <code className="bg-gray-100 px-1.5 py-0.5 rounded text-sm">
                      sky serve up
                    </code>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>
    </div>
  );
}
