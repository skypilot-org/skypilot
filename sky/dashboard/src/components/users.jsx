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
import {
  RotateCwIcon,
  PenIcon,
  CheckIcon,
  XIcon,
  KeyRoundIcon,
  Trash2Icon,
} from 'lucide-react';
import { Layout } from '@/components/elements/layout';
import { useMobile } from '@/hooks/useMobile';
import { Card } from '@/components/ui/card';
import { apiClient } from '@/data/connectors/client';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';

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

async function checkIsAdmin() {
  try {
    const response = await apiClient.get('/users/role');
    if (!response.ok) return false;
    const data = await response.json();
    return data.role === 'admin';
  } catch {
    return false;
  }
}

export function Users() {
  const [loading, setLoading] = useState(false);
  const refreshDataRef = useRef(null);
  const isMobile = useMobile();
  const [showCreateUser, setShowCreateUser] = useState(false);
  const [newUser, setNewUser] = useState({
    username: '',
    password: '',
    role: 'user',
  });
  const [creating, setCreating] = useState(false);
  const [permissionDenialState, setPermissionDenialState] = useState({
    open: false,
    message: '',
    userName: '',
  });
  const [userRoleCache, setUserRoleCache] = useState(null);
  const [roleLoading, setRoleLoading] = useState(false);

  const getUserRole = async () => {
    if (userRoleCache && Date.now() - userRoleCache.timestamp < 5 * 60 * 1000) {
      return userRoleCache;
    }

    setRoleLoading(true);
    try {
      const response = await apiClient.get(`/users/role`);
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to get user role');
      }
      const data = await response.json();
      const roleData = {
        role: data.role,
        name: data.name,
        timestamp: Date.now(),
      };
      setUserRoleCache(roleData);
      setRoleLoading(false);
      return roleData;
    } catch (error) {
      setRoleLoading(false);
      throw error;
    }
  };

  const checkPermissionAndAct = async (action, actionCallback) => {
    try {
      const roleData = await getUserRole();

      if (roleData.role !== 'admin') {
        setPermissionDenialState({
          open: true,
          message: action,
          userName: roleData.name.toLowerCase(),
        });
        return false;
      }

      actionCallback();
      return true;
    } catch (error) {
      console.error('Failed to check user role:', error);
      setPermissionDenialState({
        open: true,
        message: `Error: ${error.message}`,
        userName: '',
      });
      return false;
    }
  };

  const handleRefresh = () => {
    dashboardCache.invalidate(getUsers);
    dashboardCache.invalidate(getClusters);
    dashboardCache.invalidate(getManagedJobs, [{ allUsers: true }]);

    if (refreshDataRef.current) {
      refreshDataRef.current();
    }
  };

  const handleCreateUser = async () => {
    if (!newUser.username || !newUser.password) {
      alert('Username and password are required.');
      return;
    }
    setCreating(true);
    try {
      const response = await apiClient.post('/users/create', newUser);
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to create user');
      }
      alert('User created successfully.');
      setShowCreateUser(false);
      setNewUser({ username: '', password: '', role: 'user' });
      handleRefresh();
    } catch (error) {
      alert(`Error creating user: ${error.message}`);
    } finally {
      setCreating(false);
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
            className="text-sky-blue hover:text-sky-blue-bright flex items-center mr-2"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            {!isMobile && <span>Refresh</span>}
          </button>
          <button
            onClick={async () => {
              await checkPermissionAndAct('cannot create users', () => {
                setShowCreateUser(true);
              });
            }}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center border-sky-blue rounded px-2 py-1"
            title="Create User"
          >
            + Create User
          </button>
        </div>
      </div>
      <UsersTable
        refreshInterval={REFRESH_INTERVAL}
        setLoading={setLoading}
        refreshDataRef={refreshDataRef}
        checkPermissionAndAct={checkPermissionAndAct}
        roleLoading={roleLoading}
      />
      {showCreateUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-30">
          <div className="bg-white rounded shadow-lg p-6 min-w-[320px]">
            <h2 className="text-lg font-semibold mb-4">Create User</h2>
            <div className="flex flex-col gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Username
                </label>
                <input
                  className="border rounded px-2 py-1 w-full"
                  placeholder="Username"
                  value={newUser.username}
                  onChange={(e) =>
                    setNewUser({ ...newUser, username: e.target.value })
                  }
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Password
                </label>
                <input
                  className="border rounded px-2 py-1 w-full"
                  placeholder="Password"
                  type="password"
                  value={newUser.password}
                  onChange={(e) =>
                    setNewUser({ ...newUser, password: e.target.value })
                  }
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Role
                </label>
                <select
                  className="border rounded px-2 py-1 w-full"
                  value={newUser.role}
                  onChange={(e) =>
                    setNewUser({ ...newUser, role: e.target.value })
                  }
                >
                  <option value="user">User</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button
                className="text-sky-blue hover:underline"
                disabled={creating}
                onClick={handleCreateUser}
              >
                Create
              </button>
              <button
                className="text-gray-400 hover:text-gray-700"
                disabled={creating}
                onClick={() => setShowCreateUser(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      <Dialog
        open={permissionDenialState.open}
        onOpenChange={(open) =>
          setPermissionDenialState((prev) => ({ ...prev, open }))
        }
      >
        <DialogContent className="sm:max-w-md transition-all duration-200 ease-in-out">
          <DialogHeader>
            <DialogTitle>Permission Denied</DialogTitle>
            <DialogDescription>
              {roleLoading ? (
                <div className="flex items-center py-2">
                  <CircularProgress size={16} className="mr-2" />
                  <span>Checking permissions...</span>
                </div>
              ) : (
                <>
                  {permissionDenialState.userName ? (
                    <>
                      {permissionDenialState.userName} is logged in as non-admin
                      and {permissionDenialState.message}.
                    </>
                  ) : (
                    permissionDenialState.message
                  )}
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() =>
                setPermissionDenialState((prev) => ({ ...prev, open: false }))
              }
              disabled={roleLoading}
            >
              OK
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function UsersTable({
  refreshInterval,
  setLoading,
  refreshDataRef,
  checkPermissionAndAct,
  roleLoading,
}) {
  const [usersWithCounts, setUsersWithCounts] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasInitiallyLoaded, setHasInitiallyLoaded] = useState(false);
  const [sortConfig, setSortConfig] = useState({
    key: 'username',
    direction: 'ascending',
  });
  const [editingUserId, setEditingUserId] = useState(null);
  const [currentEditingRole, setCurrentEditingRole] = useState('');
  const [resetUserId, setResetUserId] = useState(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetLoading, setResetLoading] = useState(false);
  const [currentUserId, setCurrentUserId] = useState(null);
  const [currentUserRole, setCurrentUserRole] = useState(null);

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

  useEffect(() => {
    // Get current user info
    async function fetchCurrentUser() {
      try {
        const response = await apiClient.get('/users/role');
        if (response.ok) {
          const data = await response.json();
          setCurrentUserId(data.id);
          setCurrentUserRole(data.role);
        }
      } catch (e) {
        // ignore
      }
    }
    fetchCurrentUser();
  }, []);

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
    await checkPermissionAndAct('cannot edit user role', () => {
      setEditingUserId(userId);
      setCurrentEditingRole(currentRole);
    });
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

  const handleResetPassword = async (userId) => {
    const password = prompt('Enter new password for this user:');
    if (!password) return;
    try {
      const response = await apiClient.post('/users/update', {
        user_id: userId,
        password,
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to reset password');
      }
      alert('Password reset successfully.');
    } catch (error) {
      alert(`Error resetting password: ${error.message}`);
    }
  };

  const handleDeleteUser = async (userId) => {
    await checkPermissionAndAct('cannot delete users', async () => {
      if (!window.confirm('Are you sure you want to delete this user?')) return;
      try {
        const response = await apiClient.post('/users/delete', {
          user_id: userId,
        });
        if (!response.ok) {
          const errorData = await response.json();
          throw new Error(errorData.detail || 'Failed to delete user');
        }
        alert('User deleted successfully.');
        dashboardCache.invalidate(getUsers);
        await fetchDataAndProcess(true);
      } catch (error) {
        alert(`Error deleting user: ${error.message}`);
      }
    });
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
            <TableHead className="whitespace-nowrap w-1/6">Actions</TableHead>
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
                ) : user.clusterCount > 0 ? (
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
                {user.jobCount === -1 ? (
                  <span className="px-2 py-0.5 bg-gray-100 text-gray-400 rounded text-xs font-medium flex items-center">
                    <CircularProgress size={10} className="mr-1" />
                    Loading...
                  </span>
                ) : user.jobCount > 0 ? (
                  <span className="px-2 py-0.5 bg-green-100 text-green-800 rounded text-xs font-medium">
                    {user.jobCount}
                  </span>
                ) : (
                  <span className="px-2 py-0.5 bg-gray-100 text-gray-500 rounded text-xs font-medium">
                    0
                  </span>
                )}
              </TableCell>
              <TableCell className="relative">
                <div className="flex items-center gap-2">
                  <button
                    onClick={async () => {
                      // Check permissions
                      if (
                        currentUserRole !== 'admin' &&
                        user.userId !== currentUserId
                      ) {
                        await checkPermissionAndAct(
                          'cannot reset password for other users',
                          () => {
                            setResetUserId(user.userId);
                            setResetPassword('');
                          }
                        );
                        return;
                      }
                      setResetUserId(user.userId);
                      setResetPassword('');
                    }}
                    className="text-gray-400 hover:text-sky-blue p-1"
                    title="Reset Password"
                  >
                    <KeyRoundIcon className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => handleDeleteUser(user.userId)}
                    className="text-gray-400 hover:text-red-500 p-1"
                    title="Delete User"
                  >
                    <Trash2Icon className="h-4 w-4" />
                  </button>
                </div>
                {resetUserId === user.userId && (
                  <div
                    className="absolute z-50 bg-white border rounded shadow-lg p-3 flex flex-col gap-2"
                    style={{ top: '2.5rem', left: 0, minWidth: 220 }}
                  >
                    <input
                      type="password"
                      className="border rounded px-2 py-1 w-full"
                      placeholder="New password"
                      value={resetPassword}
                      onChange={(e) => setResetPassword(e.target.value)}
                      autoFocus
                    />
                    <div className="flex gap-2 justify-end">
                      <button
                        className="text-sky-blue hover:underline"
                        disabled={resetLoading}
                        onClick={async () => {
                          setResetLoading(true);
                          try {
                            const response = await apiClient.post(
                              '/users/update',
                              {
                                user_id: user.userId,
                                password: resetPassword,
                              }
                            );
                            if (!response.ok) {
                              const errorData = await response.json();
                              throw new Error(
                                errorData.detail || 'Failed to reset password'
                              );
                            }
                            alert('Password reset successfully.');
                            setResetUserId(null);
                          } catch (error) {
                            alert(`Error resetting password: ${error.message}`);
                          } finally {
                            setResetLoading(false);
                          }
                        }}
                      >
                        Save
                      </button>
                      <button
                        className="text-gray-400 hover:text-gray-700"
                        disabled={resetLoading}
                        onClick={() => setResetUserId(null)}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
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
  checkPermissionAndAct: PropTypes.func.isRequired,
  roleLoading: PropTypes.bool.isRequired,
};
