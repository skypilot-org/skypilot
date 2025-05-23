'use client';

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
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
import { getUsers } from '@/data/connectors/users';
import { getClusters } from '@/data/connectors/clusters'; // For fetching all clusters
import { getManagedJobs } from '@/data/connectors/jobs'; // For fetching all jobs
import { sortData } from '@/data/utils';
import { RotateCwIcon, ExternalLinkIcon } from 'lucide-react';
import { Layout } from '@/components/elements/layout';
import { useMobile } from '@/hooks/useMobile';
import { Card } from '@/components/ui/card';

const REFRESH_INTERVAL = 30000; // 30 seconds

// Helper to parse username
const parseUsername = (username) => {
  if (username && username.includes('@')) {
    return username.split('@')[0];
  }
  return username || 'N/A';
};

const getFullEmail = (username) => {
  if (username && username.includes('@')) {
    return username;
  }
  return '-';
};

export function Users() {
  const [loading, setLoading] = useState(false);
  const refreshDataRef = useRef(null);
  const isMobile = useMobile();

  const handleRefresh = () => {
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
          <Button
            variant="ghost"
            onClick={handleRefresh}
            disabled={loading}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center px-3 py-1.5 text-sm"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            {!isMobile && <span>Refresh</span>}
          </Button>
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
  const [sortConfig, setSortConfig] = useState({ key: 'username', direction: 'ascending' });

  const fetchDataAndProcess = useCallback(async () => {
    if (setLoading) setLoading(true); // Use parent setLoading if available
    setIsLoading(true);
    try {
      const [usersData, clustersData, jobsResponse] = await Promise.all([
        getUsers(),
        getClusters(), // Fetches all clusters
        getManagedJobs() // Fetches all jobs
      ]);

      const jobsData = jobsResponse.jobs || [];

      const processedUsers = (usersData || []).map(user => {
        const userClusters = (clustersData || []).filter(
          c => c.user_hash === user.userId || c.user === user.username // Match by hash or name as fallback
        );
        const userJobs = (jobsData || []).filter(
          j => j.user_hash === user.userId || j.user === user.username // Match by hash or name as fallback
        );
        return {
          ...user,
          usernameDisplay: parseUsername(user.username),
          fullEmail: getFullEmail(user.username),
          clusterCount: userClusters.length,
          jobCount: userJobs.length,
        };
      });
      setUsersWithCounts(processedUsers);
    } catch (error) {
      console.error('Failed to fetch or process user data:', error);
      setUsersWithCounts([]);
    }
    if (setLoading) setLoading(false);
    setIsLoading(false);
  }, [setLoading]);

  useEffect(() => {
    if (refreshDataRef) {
      refreshDataRef.current = fetchDataAndProcess;
    }
  }, [refreshDataRef, fetchDataAndProcess]);

  useEffect(() => {
    fetchDataAndProcess();
    const interval = setInterval(fetchDataAndProcess, refreshInterval);
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

  if (!sortedUsers || sortedUsers.length === 0) {
    return (
      <div className="text-center py-12">
        <p className="text-lg font-semibold text-gray-500">No users found.</p>
        <p className="text-sm text-gray-400 mt-1">There are currently no users to display.</p>
      </div>
    );
  }

  return (
    <Card>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead onClick={() => requestSort('usernameDisplay')} className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/4">
              Name{getSortDirection('usernameDisplay')}
            </TableHead>
            <TableHead onClick={() => requestSort('fullEmail')} className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/4">
              Email{getSortDirection('fullEmail')}
            </TableHead>
            <TableHead onClick={() => requestSort('clusterCount')} className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/4">
              Clusters{getSortDirection('clusterCount')}
            </TableHead>
            <TableHead onClick={() => requestSort('jobCount')} className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/4">
              Jobs{getSortDirection('jobCount')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sortedUsers.map((user) => (
            <TableRow key={user.userId}>
              <TableCell className="truncate" title={user.username}>{user.usernameDisplay}</TableCell>
              <TableCell className="truncate" title={user.fullEmail}>{user.fullEmail}</TableCell>
              <TableCell>
                {user.clusterCount} {user.clusterCount === 1 ? 'cluster' : 'clusters'}
              </TableCell>
              <TableCell>
                {user.jobCount} {user.jobCount === 1 ? 'active job' : 'active jobs'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
} 