'use client';

import React, {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
} from 'react';
import PropTypes from 'prop-types';
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
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
import dashboardCache from '@/lib/cache';
import cachePreloader from '@/lib/cache-preloader';
import { REFRESH_INTERVALS } from '@/lib/config';
import { sortData } from '@/data/utils';
import { RotateCwIcon, PenIcon, CheckIcon, XIcon } from 'lucide-react';
import { Layout } from '@/components/elements/layout';
import { useMobile } from '@/hooks/useMobile';
import { Card } from '@/components/ui/card';
import { apiClient } from '@/data/connectors/client';

// Helper functions for username parsing
const parseUsername = (username, userId) => {
  if (username && username.includes('@')) {
    return username.split('@')[0];
  }
  // If no email, show username only
  return username || 'N/A';
};

const getFullEmailID = (username, userId) => {
  if (username && username.includes('@')) {
    return username;
  }
  return userId || '-';
};

const REFRESH_INTERVAL = REFRESH_INTERVALS.REFRESH_INTERVAL;

export function Users() {
  const [loading, setLoading] = useState(false);
  const refreshDataRef = useRef(null);
  const isMobile = useMobile();

  const handleRefresh = () => {
    // Invalidate cache to ensure fresh data is fetched
    dashboardCache.invalidate(getUsers);
    dashboardCache.invalidate(getClusters);
    dashboardCache.invalidate(getManagedJobs, [{ allUsers: true }]);

    if (refreshDataRef.current) {
      refreshDataRef.current();
    }
  };

  return (
    <>
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
    </>
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
  const [editingUserId, setEditingUserId] = useState(null);
  const [currentEditingRole, setCurrentEditingRole] = useState('');

  const fetchDataAndProcess = useCallback(
    async (showLoading = false) => {
      if (setLoading && showLoading) setLoading(true);
      if (showLoading) setIsLoading(true);
      try {
        // Step 1: Load users first and show them immediately
        const usersData = await dashboardCache.get(getUsers);

        // Show users immediately with placeholder counts
        const initialProcessedUsers = (usersData || []).map((user) => ({
          ...user,
          usernameDisplay: parseUsername(user.username, user.userId),
          fullEmailID: getFullEmailID(user.username, user.userId),
          clusterCount: -1, // Use -1 as loading indicator
          jobCount: -1, // Use -1 as loading indicator
        }));

        setUsersWithCounts(initialProcessedUsers);
        setHasInitiallyLoaded(true);

        // Clear loading indicators now that we have users
        if (setLoading && showLoading) setLoading(false);
        if (showLoading) setIsLoading(false);

        // Step 2: Load clusters and jobs in background and update counts
        const [clustersData, managedJobsResponse] = await Promise.all([
          dashboardCache.get(getClusters),
          dashboardCache.get(getManagedJobs, [{ allUsers: true }]),
        ]);

        const jobsData = managedJobsResponse.jobs || [];

        // Update users with actual counts
        const finalProcessedUsers = (usersData || []).map((user) => {
          const userClusters = (clustersData || []).filter(
            (c) => c.user_hash === user.userId
          );
          const userJobs = (jobsData || []).filter(
            (j) => j.user_hash === user.userId
          );
          return {
            ...user,
            usernameDisplay: parseUsername(user.username, user.userId),
            fullEmailID: getFullEmailID(user.username, user.userId),
            clusterCount: userClusters.length,
            jobCount: userJobs.length,
          };
        });

        setUsersWithCounts(finalProcessedUsers);
      } catch (error) {
        console.error('Failed to fetch or process user data:', error);
        setUsersWithCounts([]);
        setHasInitiallyLoaded(true);
        if (setLoading && showLoading) setLoading(false);
        if (showLoading) setIsLoading(false);
      }
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

  const handleEditClick = async (userId, currentRole) => {
    try {
      // Get current user's role first
      const response = await apiClient.get(`/users/role`);
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to get user role');
      }
      const data = await response.json();
      console.log('data', data);
      const currentUserRole = data.role;

      if (currentUserRole != 'admin') {
        alert(`${data.name} is logged in as no admin, cannot edit user role.`);
        return;
      }

      setEditingUserId(userId);
      setCurrentEditingRole(currentRole);
    } catch (error) {
      console.error('Failed to check user role:', error);
      alert(`Error: ${error.message}`);
    }
  };

  const handleCancelEdit = () => {
    setEditingUserId(null);
    setCurrentEditingRole('');
  };

  const handleSaveEdit = async (userId) => {
    if (!userId || !currentEditingRole) {
      console.error('User ID or role is missing.');
      alert('Error: User ID or role is missing.');
      return;
    }
    setIsLoading(true); // Or use parent setLoading
    try {
      const response = await apiClient.post(`/users/update`, {
        user_id: userId,
        role: currentEditingRole,
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to update role');
      }
      // Invalidate cache before fetching new data
      dashboardCache.invalidate(getUsers);
      await fetchDataAndProcess(true); // Refresh data
      handleCancelEdit(); // Exit edit mode
    } catch (error) {
      console.error('Failed to update user role:', error);
      alert(`Error updating role: ${error.message}`);
    } finally {
      setIsLoading(false); // Or use parent setLoading
    }
  };

  if (isLoading && usersWithCounts.length === 0 && !hasInitiallyLoaded) {
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
              className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/5"
            >
              Name{getSortDirection('usernameDisplay')}
            </TableHead>
            <TableHead
              onClick={() => requestSort('fullEmailID')}
              className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/5"
            >
              User ID{getSortDirection('fullEmailID')}
            </TableHead>
            <TableHead
              onClick={() => requestSort('role')}
              className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/5"
            >
              Role{getSortDirection('role')}
            </TableHead>
            <TableHead
              onClick={() => requestSort('clusterCount')}
              className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/5"
            >
              Clusters{getSortDirection('clusterCount')}
            </TableHead>
            <TableHead
              onClick={() => requestSort('jobCount')}
              className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50 w-1/5"
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
              <TableCell className="truncate" title={user.fullEmailID}>
                {user.fullEmailID}
              </TableCell>
              <TableCell className="truncate" title={user.role}>
                <div className="flex items-center gap-2">
                  {editingUserId === user.userId ? (
                    <>
                      <select
                        value={currentEditingRole}
                        onChange={(e) => setCurrentEditingRole(e.target.value)}
                        className="block w-auto p-1 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-sky-blue focus:border-sky-blue sm:text-sm"
                      >
                        <option value="admin">Admin</option>
                        <option value="user">User</option>
                      </select>
                      <button
                        onClick={() => handleSaveEdit(user.userId)}
                        className="text-green-600 hover:text-green-800 p-1"
                        title="Save"
                      >
                        <CheckIcon className="h-4 w-4" />
                      </button>
                      <button
                        onClick={handleCancelEdit}
                        className="text-gray-500 hover:text-gray-700 p-1"
                        title="Cancel"
                      >
                        <XIcon className="h-4 w-4" />
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="capitalize">{user.role}</span>
                      <button
                        onClick={() => handleEditClick(user.userId, user.role)}
                        className="text-gray-400 hover:text-sky-blue p-1"
                        title="Edit role"
                      >
                        <PenIcon className="h-3 w-3" />
                      </button>
                    </>
                  )}
                </div>
              </TableCell>
              <TableCell>
                {user.clusterCount === -1 ? (
                  <span className="px-2 py-0.5 bg-gray-100 text-gray-400 rounded text-xs font-medium flex items-center">
                    <CircularProgress size={10} className="mr-1" />
                    Loading...
                  </span>
                ) : (
                  <Link
                    href={`/clusters?user=${encodeURIComponent(user.userId)}`}
                    className={`px-2 py-0.5 rounded text-xs font-medium transition-colors duration-200 cursor-pointer inline-block ${
                      user.clusterCount > 0
                        ? 'bg-blue-100 text-blue-800 hover:bg-blue-200 hover:text-blue-900'
                        : 'bg-gray-100 text-gray-500 hover:bg-gray-200 hover:text-gray-700'
                    }`}
                    title={`View ${user.clusterCount} cluster${user.clusterCount !== 1 ? 's' : ''} for ${user.usernameDisplay}`}
                  >
                    {user.clusterCount}
                  </Link>
                )}
              </TableCell>
              <TableCell>
                {user.jobCount === -1 ? (
                  <span className="px-2 py-0.5 bg-gray-100 text-gray-400 rounded text-xs font-medium flex items-center">
                    <CircularProgress size={10} className="mr-1" />
                    Loading...
                  </span>
                ) : (
                  <Link
                    href={`/jobs?user=${encodeURIComponent(user.userId)}`}
                    className={`px-2 py-0.5 rounded text-xs font-medium transition-colors duration-200 cursor-pointer inline-block ${
                      user.jobCount > 0
                        ? 'bg-green-100 text-green-800 hover:bg-green-200 hover:text-green-900'
                        : 'bg-gray-100 text-gray-500 hover:bg-gray-200 hover:text-gray-700'
                    }`}
                    title={`View ${user.jobCount} job${user.jobCount !== 1 ? 's' : ''} for ${user.usernameDisplay}`}
                  >
                    {user.jobCount}
                  </Link>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

UsersTable.propTypes = {
  refreshInterval: PropTypes.number.isRequired,
  setLoading: PropTypes.func.isRequired,
  refreshDataRef: PropTypes.shape({
    current: PropTypes.func,
  }).isRequired,
};
