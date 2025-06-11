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
  EyeIcon,
  EyeOffIcon,
  UploadIcon,
  DownloadIcon,
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
import { ErrorDisplay } from '@/components/elements/ErrorDisplay';

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

// Success display component
const SuccessDisplay = ({ message, onDismiss }) => {
  if (!message) return null;

  return (
    <div className="bg-green-50 border border-green-200 rounded p-4 mb-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center">
          <div className="flex-shrink-0">
            <svg
              className="h-5 w-5 text-green-400"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fillRule="evenodd"
                d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                clipRule="evenodd"
              />
            </svg>
          </div>
          <div className="ml-3">
            <p className="text-sm font-medium text-green-800">{message}</p>
          </div>
        </div>
        {onDismiss && (
          <div className="ml-auto pl-3">
            <div className="-mx-1.5 -my-1.5">
              <button
                type="button"
                onClick={onDismiss}
                className="inline-flex rounded-md bg-green-50 p-1.5 text-green-500 hover:bg-green-100 focus:outline-none focus:ring-2 focus:ring-green-600 focus:ring-offset-2 focus:ring-offset-green-50"
              >
                <span className="sr-only">Dismiss</span>
                <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                  <path
                    fillRule="evenodd"
                    d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                    clipRule="evenodd"
                  />
                </svg>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

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
  const [showPassword, setShowPassword] = useState(false);
  const [showImportExportDialog, setShowImportExportDialog] = useState(false);
  const [csvFile, setCsvFile] = useState(null);
  const [importing, setImporting] = useState(false);
  const [importResults, setImportResults] = useState(null);
  const [activeTab, setActiveTab] = useState('import');
  const [showResetPasswordDialog, setShowResetPasswordDialog] = useState(false);
  const [resetPasswordUser, setResetPasswordUser] = useState(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetLoading, setResetLoading] = useState(false);
  const [resetError, setResetError] = useState(null);
  const [showDeleteConfirmDialog, setShowDeleteConfirmDialog] = useState(false);
  const [userToDelete, setUserToDelete] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [createSuccess, setCreateSuccess] = useState(null);
  const [createError, setCreateError] = useState(null);

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
      setCreateError(new Error('Username and password are required.'));
      setShowCreateUser(false);
      return;
    }
    setCreating(true);
    setCreateError(null);
    setCreateSuccess(null);
    try {
      const response = await apiClient.post('/users/create', newUser);
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to create user');
      }
      setCreateSuccess(`User &quot;${newUser.username}&quot; created successfully!`);
      setShowCreateUser(false);
      setNewUser({ username: '', password: '', role: 'user' });
      handleRefresh();
    } catch (error) {
      setCreateError(error);
      setShowCreateUser(false);
      setNewUser({ username: '', password: '', role: 'user' });
    } finally {
      setCreating(false);
    }
  };

  const handleFileUpload = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    setCsvFile(file);
    setImportResults(null);
  };

  const handleImportUsers = async () => {
    if (!csvFile) {
      alert('Please select a CSV file first.');
      return;
    }

    setImporting(true);
    try {
      const reader = new FileReader();
      reader.onload = async (e) => {
        try {
          const csvContent = e.target.result;
          const response = await apiClient.post('/users/import', { 
            csv_content: csvContent 
          });
          
          if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to import users');
          }
          
          const results = await response.json();
          
          // Create user-friendly message
          let message = `Import completed. ${results.success_count} users created successfully.`;
          if (results.error_count > 0) {
            message += `\n${results.error_count} failed.`;
            if (results.creation_errors.length > 0) {
              message += `\nErrors: ${results.creation_errors.slice(0, 3).join(', ')}`;
              if (results.creation_errors.length > 3) {
                message += ` and ${results.creation_errors.length - 3} more...`;
              }
            }
          }
          
          setImportResults({ message });
          if (results.success_count > 0) {
            handleRefresh();
          }
        } catch (error) {
          alert(`Error importing users: ${error.message}`);
        } finally {
          setImporting(false);
        }
      };
      reader.readAsText(csvFile);
    } catch (error) {
      alert(`Error reading file: ${error.message}`);
      setImporting(false);
    }
  };

  const handleResetPasswordClick = async (user) => {
    setResetPasswordUser(user);
    setResetPassword('');
    setShowResetPasswordDialog(true);
  };

  const handleResetPasswordSubmit = async () => {
    if (!resetPassword) {
      setResetError(new Error('Please enter a new password.'));
      return;
    }
    setResetLoading(true);
    setResetError(null);
    try {
      const response = await apiClient.post('/users/update', {
        user_id: resetPasswordUser.userId,
        password: resetPassword,
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to reset password');
      }
      setCreateSuccess(`Password reset successfully for user "${resetPasswordUser.usernameDisplay}"!`);
      setShowResetPasswordDialog(false);
      setResetPasswordUser(null);
      setResetPassword('');
    } catch (error) {
      setResetError(error);
    } finally {
      setResetLoading(false);
    }
  };

  const handleDeleteUserClick = (user) => {
    checkPermissionAndAct('cannot delete users', () => {
      setUserToDelete(user);
      setShowDeleteConfirmDialog(true);
    });
  };

  const handleDeleteUserConfirm = async () => {
    if (!userToDelete) return;
    setDeleteLoading(true);
    setDeleteError(null);
    try {
      const response = await apiClient.post('/users/delete', {
        user_id: userToDelete.userId,
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to delete user');
      }
      setCreateSuccess(`User "${userToDelete.usernameDisplay}" deleted successfully!`);
      setShowDeleteConfirmDialog(false);
      setUserToDelete(null);
      handleRefresh();
    } catch (error) {
      setDeleteError(error);
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleCancelDelete = () => {
    setShowDeleteConfirmDialog(false);
    setUserToDelete(null);
    setDeleteError(null);
  };

  const handleCancelResetPassword = () => {
    setShowResetPasswordDialog(false);
    setResetPasswordUser(null);
    setResetPassword('');
    setResetError(null);
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
            onClick={async () => {
              await checkPermissionAndAct('cannot create users', () => {
                setShowCreateUser(true);
              });
            }}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center border-sky-blue rounded px-2 py-1 mr-2"
            title="Create New User"
          >
            + New User
          </button>
          <button
            onClick={async () => {
              await checkPermissionAndAct('cannot import users', () => {
                setShowImportExportDialog(true);
              });
            }}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center rounded px-2 py-1 mr-2"
            title="Import/Export Users"
          >
            <UploadIcon className="h-4 w-4 mr-1" />
            Import/Export
          </button>
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

      {/* Success and Error Messages */}
      <SuccessDisplay 
        message={createSuccess} 
        onDismiss={() => setCreateSuccess(null)} 
      />
      <ErrorDisplay
        error={createError}
        title="Error"
        onDismiss={() => setCreateError(null)}
      />

      <UsersTable
        refreshInterval={REFRESH_INTERVAL}
        setLoading={setLoading}
        refreshDataRef={refreshDataRef}
        checkPermissionAndAct={checkPermissionAndAct}
        roleLoading={roleLoading}
        onResetPassword={handleResetPasswordClick}
        onDeleteUser={handleDeleteUserClick}
      />

      {/* Create User Dialog */}
      <Dialog open={showCreateUser} onOpenChange={setShowCreateUser}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Create User</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-4">
            <div className="grid gap-2">
              <label className="text-sm font-medium text-gray-700">
                Username
              </label>
              <input
                className="border rounded px-3 py-2 w-full"
                placeholder="Username"
                value={newUser.username}
                onChange={(e) =>
                  setNewUser({ ...newUser, username: e.target.value })
                }
              />
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium text-gray-700">
                Password
              </label>
              <div className="relative">
                <input
                  className="border rounded px-3 py-2 w-full pr-10"
                  placeholder="Password"
                  type={showPassword ? 'text' : 'password'}
                  value={newUser.password}
                  onChange={(e) =>
                    setNewUser({ ...newUser, password: e.target.value })
                  }
                />
                <button
                  type="button"
                  className="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? (
                    <EyeOffIcon className="h-4 w-4" />
                  ) : (
                    <EyeIcon className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium text-gray-700">Role</label>
              <select
                className="border rounded px-3 py-2 w-full"
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
          <DialogFooter>
            <button
              className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-10 px-4 py-2"
              onClick={() => setShowCreateUser(false)}
              disabled={creating}
            >
              Cancel
            </button>
            <button
              className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-sky-600 text-white hover:bg-sky-700 h-10 px-4 py-2"
              onClick={handleCreateUser}
              disabled={creating}
            >
              {creating ? 'Creating...' : 'Create'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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

      {/* Import/Export Users Dialog */}
      <Dialog open={showImportExportDialog} onOpenChange={setShowImportExportDialog}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Import/Export Users</DialogTitle>
          </DialogHeader>
          
          {/* Tabs */}
          <div className="flex border-b border-gray-200 mb-4">
            <button
              className={`px-4 py-2 text-sm font-medium ${
                activeTab === 'import'
                  ? 'border-b-2 border-sky-500 text-sky-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
              onClick={() => setActiveTab('import')}
            >
              Import
            </button>
            <button
              className={`px-4 py-2 text-sm font-medium ${
                activeTab === 'export'
                  ? 'border-b-2 border-sky-500 text-sky-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
              onClick={() => setActiveTab('export')}
            >
              Export
            </button>
          </div>

          <div className="flex flex-col gap-4 py-4">
            {activeTab === 'import' ? (
              <>
                <div className="grid gap-2">
                  <label className="text-sm font-medium text-gray-700">
                    CSV File
                  </label>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={handleFileUpload}
                    className="border rounded px-3 py-2 w-full"
                  />
                  <p className="text-xs text-gray-500">
                    CSV should have columns: username, password, role<br/>
                    Supports both plain text passwords and exported password hashes.
                  </p>
                </div>
                
                {importResults && (
                  <div className="p-3 bg-green-50 border border-green-200 rounded text-green-700 text-sm">
                    {importResults.message}
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="grid gap-2">
                  <label className="text-sm font-medium text-gray-700">
                    Export Users to CSV
                  </label>
                  <p className="text-xs text-gray-500">
                    Download all users as a CSV file with password hashes.
                  </p>
                  <div className="p-3 bg-amber-50 border border-amber-200 rounded">
                    <p className="text-sm text-amber-700">
                      ⚠️ This will export all users with columns: username, password (hashed), role
                    </p>
                    <p className="text-xs text-amber-600 mt-1">
                      Password hashes can be imported directly for system backups.
                    </p>
                  </div>
                </div>
              </>
            )}
          </div>
          
          <DialogFooter>
            <button
              className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-10 px-4 py-2"
              onClick={() => setShowImportExportDialog(false)}
              disabled={importing}
            >
              Cancel
            </button>
            {activeTab === 'import' ? (
              <button
                className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-sky-600 text-white hover:bg-sky-700 h-10 px-4 py-2"
                onClick={handleImportUsers}
                disabled={importing || !csvFile}
              >
                {importing ? 'Importing...' : 'Import'}
              </button>
            ) : (
              <button
                className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-sky-600 text-white hover:bg-sky-700 h-10 px-4 py-2"
                onClick={async () => {
                  try {
                    const response = await apiClient.get('/users/export');
                    if (!response.ok) {
                      const errorData = await response.json();
                      throw new Error(errorData.detail || 'Failed to export users');
                    }
                    
                    const data = await response.json();
                    const csvContent = data.csv_content;
                    
                    // Download the CSV file
                    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = `users_export_${new Date().toISOString().split('T')[0]}.csv`;
                    link.click();
                    URL.revokeObjectURL(url);
                    
                    // Show success message
                    alert(`Successfully exported ${data.user_count} users to CSV file.`);
                  } catch (error) {
                    alert(`Error exporting users: ${error.message}`);
                  }
                }}
              >
                <DownloadIcon className="h-4 w-4 mr-1" />
                Export
              </button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reset Password Dialog */}
      <Dialog open={showResetPasswordDialog} onOpenChange={handleCancelResetPassword}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Reset Password</DialogTitle>
            <DialogDescription>
              Enter a new password for {resetPasswordUser?.usernameDisplay || 'this user'}.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4 py-4">
            <div className="grid gap-2">
              <label className="text-sm font-medium text-gray-700">
                New Password
              </label>
              <input
                type="password"
                className="border rounded px-3 py-2 w-full"
                placeholder="Enter new password"
                value={resetPassword}
                onChange={(e) => setResetPassword(e.target.value)}
                autoFocus
              />
            </div>
          </div>

          <ErrorDisplay
            error={resetError}
            title="Reset Failed"
            onDismiss={() => setResetError(null)}
          />

          <DialogFooter>
            <Button
              variant="outline"
              onClick={handleCancelResetPassword}
              disabled={resetLoading}
            >
              Cancel
            </Button>
            <Button
              variant="default"
              onClick={handleResetPasswordSubmit}
              disabled={resetLoading || !resetPassword}
              className="bg-sky-600 text-white hover:bg-sky-700"
            >
              {resetLoading ? 'Resetting...' : 'Reset Password'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete User Confirmation Dialog */}
      <Dialog open={showDeleteConfirmDialog} onOpenChange={handleCancelDelete}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete User</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete user "{userToDelete?.usernameDisplay || 'this user'}"? This action cannot be undone.
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
              onClick={handleDeleteUserConfirm}
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

function UsersTable({
  refreshInterval,
  setLoading,
  refreshDataRef,
  checkPermissionAndAct,
  roleLoading,
  onResetPassword,
  onDeleteUser,
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
                        className="text-sky-blue hover:text-sky-blue-bright p-1"
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
                            onResetPassword(user);
                          }
                        );
                        return;
                      }
                      onResetPassword(user);
                    }}
                    className="text-sky-blue hover:text-sky-blue-bright p-1"
                    title="Reset Password"
                  >
                    <KeyRoundIcon className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => onDeleteUser(user)}
                    className="text-sky-blue hover:text-red-500 p-1"
                    title="Delete User"
                  >
                    <Trash2Icon className="h-4 w-4" />
                  </button>
                </div>
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
  onResetPassword: PropTypes.func.isRequired,
  onDeleteUser: PropTypes.func.isRequired,
};
