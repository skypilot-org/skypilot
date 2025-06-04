'use client';

import React, {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
} from 'react';
import { CircularProgress } from '@mui/material';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { getUsersWithCounts } from '@/data/connectors/users';
import dashboardCache from '@/lib/cache';
import cachePreloader from '@/lib/cache-preloader';
import { REFRESH_INTERVALS } from '@/lib/config';
import { sortData } from '@/data/utils';
import { RotateCwIcon, ExternalLinkIcon } from 'lucide-react';
import { Layout } from '@/components/elements/layout';
import { useMobile } from '@/hooks/useMobile';
import { Card } from '@/components/ui/card';

const REFRESH_INTERVAL = REFRESH_INTERVALS.REFRESH_INTERVAL;

export function Users() {
  const [loading, setLoading] = useState(false);
  const refreshDataRef = useRef(null);
  const isMobile = useMobile();

  const handleRefresh = () => {
    // Invalidate cache to ensure fresh data is fetched
    dashboardCache.invalidate(getUsersWithCounts);

    if (refreshDataRef.current) {
      refreshDataRef.current();
    }
  };

  return (
    <Layout highlighted="users">
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base">
          <Link
            href="/users"
            className="text-sky-blue hover:underline leading-none"
          >
            Users
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
      <UsersTable
        refreshInterval={REFRESH_INTERVAL}
        setLoading={setLoading} // Pass setLoading to UsersTable
        refreshDataRef={refreshDataRef}
      />
    </Layout>
  );
}

function UsersTable({ refreshInterval, setLoading, refreshDataRef }) {
  const [usersWithCounts, setUsersWithCounts] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasInitiallyLoaded, setHasInitiallyLoaded] = useState(false);
  const [sortConfig, setSortConfig] = useState({
    key: 'username',
    direction: 'ascending',
  });

  const fetchDataAndProcess = useCallback(
    async (showLoading = false) => {
      if (setLoading && showLoading) setLoading(true); // Use parent setLoading if available
      if (showLoading) setIsLoading(true);
      try {
        const processedUsers = await dashboardCache.get(getUsersWithCounts);
        setUsersWithCounts(processedUsers);
        setHasInitiallyLoaded(true);
      } catch (error) {
        console.error('Failed to fetch or process user data:', error);
        setUsersWithCounts([]);
        setHasInitiallyLoaded(true);
      }
      if (setLoading && showLoading) setLoading(false);
      if (showLoading) setIsLoading(false);
    },
    [setLoading]
  );

  useEffect(() => {
    if (refreshDataRef) {
      refreshDataRef.current = () => fetchDataAndProcess(true); // Show loading on manual refresh
    }
  }, [refreshDataRef, fetchDataAndProcess]);

  useEffect(() => {
    const initializeData = async () => {
      // Reset loading state when component mounts
      setHasInitiallyLoaded(false);
      setIsLoading(true);

      // Trigger cache preloading for users page and background preload other pages
      await cachePreloader.preloadForPage('users');

      fetchDataAndProcess(true); // Show loading on initial load
    };

    initializeData();

    const interval = setInterval(() => {
      fetchDataAndProcess(false); // Don't show loading on background refresh
    }, refreshInterval);
    return () => clearInterval(interval);
  }, [fetchDataAndProcess, refreshInterval]);

  const sortedUsers = useMemo(() => {
    return sortData(usersWithCounts, sortConfig.key, sortConfig.direction);
  }, [usersWithCounts, sortConfig]);

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

  if (isLoading && usersWithCounts.length === 0) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress />
      </div>
    );
  }

  if (!hasInitiallyLoaded) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress />
        <span className="ml-2 text-gray-500">Loading users...</span>
      </div>
    );
  }

  if (!sortedUsers || sortedUsers.length === 0) {
    return (
      <div className="text-center py-12">
        <p className="text-lg font-semibold text-gray-500">No users found.</p>
        <p className="text-sm text-gray-400 mt-1">
          There are currently no users to display.
        </p>
      </div>
    );
  }

  return (
    <Card>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead
              onClick={() => requestSort('usernameDisplay')}
              className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/4"
            >
              Name{getSortDirection('usernameDisplay')}
            </TableHead>
            <TableHead
              onClick={() => requestSort('fullEmail')}
              className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/4"
            >
              Email{getSortDirection('fullEmail')}
            </TableHead>
            <TableHead
              onClick={() => requestSort('clusterCount')}
              className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/4"
            >
              Clusters{getSortDirection('clusterCount')}
            </TableHead>
            <TableHead
              onClick={() => requestSort('jobCount')}
              className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/4"
            >
              Jobs{getSortDirection('jobCount')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sortedUsers.map((user) => (
            <TableRow key={user.userId}>
              <TableCell className="truncate" title={user.username}>
                {user.usernameDisplay}
              </TableCell>
              <TableCell className="truncate" title={user.fullEmail}>
                {user.fullEmail}
              </TableCell>
              <TableCell>
                {user.clusterCount > 0 ? (
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-800 rounded text-xs font-medium">
                    {user.clusterCount}
                  </span>
                ) : (
                  <span className="px-2 py-0.5 bg-gray-100 text-gray-500 rounded text-xs font-medium">
                    0
                  </span>
                )}
              </TableCell>
              <TableCell>
                {user.jobCount > 0 ? (
                  <span className="px-2 py-0.5 bg-green-100 text-green-800 rounded text-xs font-medium">
                    {user.jobCount}
                  </span>
                ) : (
                  <span className="px-2 py-0.5 bg-gray-100 text-gray-500 rounded text-xs font-medium">
                    0
                  </span>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}
