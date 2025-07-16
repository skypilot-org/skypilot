'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/router';
import {
  getWorkspaces,
  updateWorkspace,
  createWorkspace,
  deleteWorkspace,
  getEnabledClouds,
} from '@/data/connectors/workspaces';
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
import { Layout } from '@/components/elements/layout';
import Link from 'next/link';
import Head from 'next/head';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { CircularProgress } from '@mui/material';
import {
  SaveIcon,
  TrashIcon,
  CheckIcon,
  RotateCwIcon,
  User,
} from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  ServerIcon,
  BriefcaseIcon,
  TickIcon,
  StarIcon,
} from '@/components/elements/icons';
import { ErrorDisplay } from '@/components/elements/ErrorDisplay';
import { statusGroups } from './jobs'; // Import statusGroups
import yaml from 'js-yaml';
import { CLOUD_CANONICALIZATIONS } from '@/data/connectors/constants';
import { getUsers } from '@/data/connectors/users';

// Success display component
const SuccessDisplay = ({ message }) => {
  if (!message) return null;

  return (
    <Alert className="border-green-200 bg-green-50">
      <CheckIcon className="h-4 w-4 text-green-600" />
      <AlertDescription className="text-green-800">{message}</AlertDescription>
    </Alert>
  );
};

// Workspace configuration description component
const WorkspaceConfigDescription = ({
  workspaceName,
  config,
  enabledClouds = [],
}) => {
  if (!config) return null;

  const isDefault = workspaceName === 'default';
  const isEmptyConfig = Object.keys(config).length === 0;

  if (isDefault && isEmptyConfig) {
    return (
      <div className="text-sm text-gray-500 mb-3 italic p-3 bg-sky-50 rounded border border-sky-200">
        Workspace &apos;default&apos; can use all accessible infrastructure.
      </div>
    );
  }

  const enabledDescriptions = [];
  const disabledClouds = [];
  const configuredButNotEnabled = [];

  // Convert enabledClouds to a set for faster lookup
  const enabledCloudsSet = new Set(
    enabledClouds.map((cloud) => cloud.toLowerCase())
  );

  Object.entries(config).forEach(([cloud, cloudConfig]) => {
    // Skip non-cloud configuration keys
    if (cloud === 'private' || cloud === 'allowed_users') {
      return;
    }

    const cloudName =
      CLOUD_CANONICALIZATIONS[cloud.toLowerCase()] || cloud.toUpperCase();
    const isActuallyEnabled = enabledCloudsSet.has(cloudName?.toLowerCase());

    if (cloudConfig?.disabled === true) {
      disabledClouds.push(cloudName);
    } else if (cloudConfig && Object.keys(cloudConfig).length > 0) {
      let detail = '';
      if (cloud.toLowerCase() === 'gcp' && cloudConfig.project_id) {
        detail = ` (Project ID: ${cloudConfig.project_id})`;
      } else if (cloud.toLowerCase() === 'aws' && cloudConfig.region) {
        detail = ` (Region: ${cloudConfig.region})`;
      }

      if (isActuallyEnabled) {
        enabledDescriptions.push(
          <span key={`${cloud}-enabled`} className="block">
            {cloudName}
            {detail} is enabled.
          </span>
        );
      } else {
        configuredButNotEnabled.push(
          <span
            key={`${cloud}-configured-not-enabled`}
            className="block text-amber-700"
          >
            {cloudName}
            {detail} is configured but not currently available.
          </span>
        );
      }
    } else {
      if (isActuallyEnabled) {
        enabledDescriptions.push(
          <span key={`${cloud}-default-enabled`} className="block">
            {cloudName} is enabled (using default settings).
          </span>
        );
      } else {
        configuredButNotEnabled.push(
          <span
            key={`${cloud}-default-not-enabled`}
            className="block text-amber-700"
          >
            {cloudName} is configured but not currently available.
          </span>
        );
      }
    }
  });

  const finalDescriptions = [];
  if (disabledClouds.length > 0) {
    const disabledString = disabledClouds.join(' and ');
    finalDescriptions.push(
      <span key="disabled-clouds" className="block">
        {disabledString} {disabledClouds.length === 1 ? 'is' : 'are'} explicitly
        disabled.
      </span>
    );
  }
  finalDescriptions.push(...enabledDescriptions);
  finalDescriptions.push(...configuredButNotEnabled);

  if (finalDescriptions.length > 0) {
    return (
      <div className="text-sm text-gray-700 mb-3 p-3 bg-sky-50 rounded border border-sky-200">
        {finalDescriptions}
      </div>
    );
  }

  if (!isDefault && isEmptyConfig) {
    return (
      <div className="text-sm text-gray-500 mb-3 italic p-3 bg-sky-50 rounded border border-sky-200">
        This workspace has no specific cloud resource configurations and can use
        all accessible infrastructure.
      </div>
    );
  }
  return null;
};

// Workspace badge component for private/public status
const WorkspaceBadge = ({ isPrivate }) => {
  if (isPrivate) {
    return (
      <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-700 border border-gray-300">
        Private
      </span>
    );
  }
  return (
    <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-green-100 text-green-700 border border-green-300">
      Public
    </span>
  );
};

// Detailed allowed users component for workspace editor
const DetailedAllowedUsers = ({ workspaceConfig, allUsers }) => {
  if (!workspaceConfig.private) return null;

  // Get allowed users from config
  const allowedUsersFromConfig = workspaceConfig.allowed_users || [];

  // Get all admin users
  const adminUsers = (allUsers || []).filter((user) => user.role === 'admin');
  const adminUsernames = adminUsers.map((user) => user.username);

  // Combine allowed users and admin users, remove duplicates
  const allAllowedUsers = [
    ...new Set([...allowedUsersFromConfig, ...adminUsernames]),
  ];

  if (allAllowedUsers.length === 0) {
    return (
      <div className="mt-4">
        <h4 className="mb-2 text-xs text-gray-500 tracking-wider">
          Allowed Users (0)
        </h4>
        <div className="text-amber-600 text-xs italic p-2 bg-amber-50 rounded border border-amber-200">
          No users configured (workspace may be inaccessible)
        </div>
      </div>
    );
  }

  return (
    <div className="mt-4">
      <h4 className="mb-2 text-xs text-gray-500 tracking-wider">
        Allowed Users ({allAllowedUsers.length})
      </h4>
      <div className="space-y-1 max-h-48 overflow-y-auto border border-gray-200 rounded">
        {allAllowedUsers.map((username) => {
          const isAdmin = adminUsernames.includes(username);
          return (
            <div
              key={username}
              className="flex items-center justify-between text-xs p-2 bg-gray-50 hover:bg-gray-100 border-b border-gray-100 last:border-b-0"
            >
              <span className="font-medium text-gray-700">{username}</span>
              {isAdmin ? (
                <span className="inline-flex items-center text-blue-600">
                  <StarIcon className="w-3 h-3 mr-1" />
                  Admin
                </span>
              ) : (
                <span className="inline-flex items-center text-gray-600">
                  <User className="w-3 h-3 mr-1" />
                  User
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export function WorkspaceEditor({ workspaceName, isNewWorkspace = false }) {
  const router = useRouter();
  const [workspaceConfig, setWorkspaceConfig] = useState({});
  const [originalConfig, setOriginalConfig] = useState({});
  const [yamlValue, setYamlValue] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [yamlError, setYamlError] = useState(null);
  const [allUsers, setAllUsers] = useState([]);

  // Delete state
  const [deleteState, setDeleteState] = useState({
    showDialog: false,
    deleting: false,
    error: null,
  });

  // Workspace statistics
  const [workspaceStats, setWorkspaceStats] = useState({
    totalClusterCount: 0,
    runningClusterCount: 0,
    managedJobsCount: 0,
    clouds: [],
  });
  const [statsLoading, setStatsLoading] = useState(false);

  const fetchWorkspaceConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [allWorkspaces, usersResponse] = await Promise.all([
        getWorkspaces(),
        getUsers(),
      ]);

      const config = allWorkspaces[workspaceName] || {};
      setWorkspaceConfig(config);
      setOriginalConfig(config);
      setAllUsers(usersResponse || []);

      // Format as YAML with workspace name as top-level key
      const fullConfig = { [workspaceName]: config };
      let yamlOutput;
      if (Object.keys(config).length === 0) {
        yamlOutput = `${workspaceName}:\n  # Empty workspace configuration - uses all accessible infrastructure\n`;
      } else {
        yamlOutput = yaml.dump(fullConfig, {
          indent: 2,
          lineWidth: -1,
          noRefs: true,
          skipInvalid: true,
          flowLevel: -1,
        });
      }
      setYamlValue(yamlOutput);
    } catch (err) {
      console.error('Error fetching workspace config:', err);
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [workspaceName]);

  const fetchWorkspaceStats = useCallback(async () => {
    if (isNewWorkspace) return;

    setStatsLoading(true);
    try {
      const [clustersResponse, managedJobsResponse, enabledClouds] =
        await Promise.all([
          getClusters(),
          getManagedJobs(),
          getEnabledClouds(workspaceName, true),
        ]);

      // Filter clusters for this workspace
      const workspaceClusters = clustersResponse.filter(
        (cluster) => (cluster.workspace || 'default') === workspaceName
      );

      // Count running clusters
      const runningClusters = workspaceClusters.filter(
        (cluster) =>
          cluster.status === 'RUNNING' || cluster.status === 'LAUNCHING'
      );

      // Map cluster names to workspace for job filtering
      const clusterNameToWorkspace = {};
      clustersResponse.forEach((c) => {
        clusterNameToWorkspace[c.cluster] = c.workspace || 'default';
      });

      // Count managed jobs for this workspace
      const jobs = managedJobsResponse.jobs || [];
      const activeJobStatuses = new Set(statusGroups.active);
      let managedJobsCount = 0;

      jobs.forEach((job) => {
        const jobClusterName =
          job.cluster_name || (job.resources && job.resources.cluster_name);
        if (jobClusterName) {
          const jobWorkspace = clusterNameToWorkspace[jobClusterName];
          if (
            jobWorkspace === workspaceName &&
            activeJobStatuses.has(job.status)
          ) {
            managedJobsCount++;
          }
        }
      });

      setWorkspaceStats({
        totalClusterCount: workspaceClusters.length,
        runningClusterCount: runningClusters.length,
        managedJobsCount: managedJobsCount,
        clouds: Array.isArray(enabledClouds) ? enabledClouds : [],
      });
    } catch (err) {
      console.error('Failed to fetch workspace stats:', err);
      // Don't show error to user for stats, just log it
    } finally {
      setStatsLoading(false);
    }
  }, [workspaceName, isNewWorkspace]);

  useEffect(() => {
    if (!isNewWorkspace) {
      fetchWorkspaceConfig();
      fetchWorkspaceStats();
    } else {
      setLoading(false);
      setYamlValue(
        `${workspaceName}:\n  # New workspace configuration\n  # Leave empty to use all accessible infrastructure\n`
      );
    }
  }, [
    workspaceName,
    isNewWorkspace,
    fetchWorkspaceConfig,
    fetchWorkspaceStats,
  ]);

  useEffect(() => {
    // Check for changes
    const currentConfigStr = JSON.stringify(workspaceConfig);
    const originalConfigStr = JSON.stringify(originalConfig);
    setHasChanges(currentConfigStr !== originalConfigStr);
  }, [workspaceConfig, originalConfig]);

  const handleYamlChange = (value) => {
    setYamlValue(value);
    setYamlError(null);

    try {
      const parsed = yaml.load(value) || {};

      // Validate workspace name
      const keys = Object.keys(parsed);
      if (keys.length === 0) {
        // Empty configuration is allowed
        setWorkspaceConfig({});
      } else if (keys.length === 1) {
        const parsedWorkspaceName = keys[0];
        if (parsedWorkspaceName !== workspaceName) {
          setYamlError(
            `Workspace name cannot be changed. Expected "${workspaceName}" but found "${parsedWorkspaceName}".`
          );
          return;
        }
        // Extract the configuration for this workspace
        const config = parsed[workspaceName] || {};
        setWorkspaceConfig(config);
      } else {
        setYamlError(
          `Configuration must contain only one workspace. Found: ${keys.join(', ')}`
        );
      }
    } catch (err) {
      setYamlError(`Invalid YAML: ${err.message}`);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      // Validate YAML
      if (yamlError) {
        throw new Error('Please fix YAML errors before saving');
      }

      // Additional validation: ensure workspace name hasn't been changed in YAML
      const parsed = yaml.load(yamlValue) || {};
      const keys = Object.keys(parsed);
      if (keys.length > 0 && keys[0] !== workspaceName) {
        throw new Error(
          `Workspace name cannot be changed. Expected "${workspaceName}".`
        );
      }

      if (isNewWorkspace) {
        await createWorkspace(workspaceName, workspaceConfig);
        setSuccess('Workspace created successfully!');
        // Navigate to the created workspace
        setTimeout(() => {
          router.push(`/workspaces/${workspaceName}`);
        }, 1500);
      } else {
        await updateWorkspace(workspaceName, workspaceConfig);
        setSuccess('Workspace updated successfully!');
        setOriginalConfig(workspaceConfig);
        // Refresh stats after successful save
        fetchWorkspaceStats();
      }
    } catch (err) {
      console.error('Error saving workspace:', err);
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteWorkspace = () => {
    setDeleteState({
      showDialog: true,
      deleting: false,
      error: null,
    });
  };

  const handleConfirmDelete = async () => {
    setDeleteState((prev) => ({ ...prev, deleting: true, error: null }));

    try {
      await deleteWorkspace(workspaceName);
      setSuccess('Workspace deleted successfully!');
      setTimeout(() => {
        router.push('/workspaces');
      }, 1500);
    } catch (err) {
      console.error('Error deleting workspace:', err);
      setDeleteState((prev) => ({
        ...prev,
        deleting: false,
        error: err,
      }));
    }
  };

  const handleCancelDelete = () => {
    setDeleteState({
      showDialog: false,
      deleting: false,
      error: null,
    });
  };

  const handleDiscard = () => {
    // Reset to original configuration
    setWorkspaceConfig(originalConfig);

    // Reset YAML value to original
    const fullConfig = { [workspaceName]: originalConfig };
    let yamlOutput;
    if (Object.keys(originalConfig).length === 0) {
      yamlOutput = `${workspaceName}:\n  # Empty workspace configuration - uses all accessible infrastructure\n`;
    } else {
      yamlOutput = yaml.dump(fullConfig, {
        indent: 2,
        lineWidth: -1,
        noRefs: true,
        skipInvalid: true,
        flowLevel: -1,
      });
    }
    setYamlValue(yamlOutput);

    // Clear any errors
    setYamlError(null);
    setError(null);
    setSuccess(null);
  };

  const handleRefresh = async () => {
    await Promise.all([fetchWorkspaceConfig(), fetchWorkspaceStats()]);
  };

  if (!router.isReady) {
    return <div>Loading...</div>;
  }

  const title = isNewWorkspace
    ? 'Create New Workspace | SkyPilot Dashboard'
    : `Workspace: ${workspaceName} | SkyPilot Dashboard`;

  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <>
        {/* Header with breadcrumb navigation */}
        <div className="flex items-center justify-between mb-4 h-5">
          <div className="text-base flex items-center">
            <Link href="/workspaces" className="text-sky-blue hover:underline">
              Workspaces
            </Link>
            <span className="mx-2 text-gray-500">›</span>
            <Link
              href={
                isNewWorkspace
                  ? `/workspace/new`
                  : `/workspaces/${workspaceName}`
              }
              className="text-sky-blue hover:underline"
            >
              {isNewWorkspace ? 'New Workspace' : workspaceName}
            </Link>
            {hasChanges && (
              <span className="ml-3 px-2 py-1 bg-yellow-100 text-yellow-800 text-xs rounded">
                Unsaved changes
              </span>
            )}
          </div>

          <div className="text-sm flex items-center">
            {(loading || saving || statsLoading) && (
              <div className="flex items-center mr-4">
                <CircularProgress size={15} className="mt-0" />
                <span className="ml-2 text-gray-500">
                  {saving ? 'Saving...' : 'Loading...'}
                </span>
              </div>
            )}

            <div className="flex items-center space-x-4">
              {!isNewWorkspace && (
                <button
                  onClick={handleRefresh}
                  disabled={loading || saving || statsLoading}
                  className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center"
                >
                  <RotateCwIcon className="w-4 h-4 mr-1.5" />
                  Refresh
                </button>
              )}

              {!isNewWorkspace && workspaceName !== 'default' && (
                <button
                  onClick={() =>
                    setDeleteState({ ...deleteState, showDialog: true })
                  }
                  disabled={deleteState.deleting || saving}
                  className="text-red-600 hover:text-red-700 font-medium inline-flex items-center"
                >
                  <TrashIcon className="w-4 h-4 mr-1.5" />
                  Delete
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Content */}
        {loading ? (
          <div className="flex justify-center items-center py-12">
            <CircularProgress size={24} className="mr-2" />
            <span className="text-gray-500">
              Loading workspace configuration...
            </span>
          </div>
        ) : (
          <div className="space-y-6">
            {/* Alerts */}
            <ErrorDisplay
              error={error}
              title="Error"
              onDismiss={() => setError(null)}
            />
            <SuccessDisplay message={success} />

            {/* Two-column layout */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* Left column - Workspace Details (only for existing workspaces) */}
              {!isNewWorkspace && (
                <div className="lg:col-span-1">
                  <Card className="h-full">
                    <CardHeader>
                      <CardTitle className="text-base font-normal">
                        <div className="flex items-center justify-between">
                          <div>
                            <span className="font-semibold">Workspace:</span>{' '}
                            {workspaceName}
                          </div>
                          <WorkspaceBadge
                            isPrivate={originalConfig.private === true}
                          />
                        </div>
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm pb-2 flex-1">
                      <div className="py-2 flex items-center justify-between">
                        <div className="flex items-center text-gray-600">
                          <ServerIcon className="w-4 h-4 mr-2 text-gray-500" />
                          <span>Clusters (Running / Total)</span>
                        </div>
                        <span className="font-normal text-gray-800">
                          {statsLoading
                            ? '...'
                            : `${workspaceStats.runningClusterCount} / ${workspaceStats.totalClusterCount}`}
                        </span>
                      </div>
                      <div className="py-2 flex items-center justify-between border-t border-gray-100">
                        <div className="flex items-center text-gray-600">
                          <BriefcaseIcon className="w-4 h-4 mr-2 text-gray-500" />
                          <span>Managed Jobs</span>
                        </div>
                        <span className="font-normal text-gray-800">
                          {statsLoading
                            ? '...'
                            : workspaceStats.managedJobsCount}
                        </span>
                      </div>
                    </CardContent>

                    <div className="px-6 pb-6 text-sm pt-3">
                      <h4 className="mb-2 text-xs text-gray-500 tracking-wider">
                        Enabled Infra
                      </h4>
                      <div className="flex flex-wrap gap-x-4 gap-y-1">
                        {statsLoading ? (
                          <span className="text-gray-500">Loading...</span>
                        ) : workspaceStats.clouds.length > 0 ? (
                          workspaceStats.clouds.map((cloud) => (
                            <div
                              key={cloud}
                              className="flex items-center text-gray-700"
                            >
                              <TickIcon className="w-3.5 h-3.5 mr-1.5 text-green-500" />
                              <span>{cloud}</span>
                            </div>
                          ))
                        ) : (
                          <span className="text-gray-500 italic">
                            No enabled infrastructure
                          </span>
                        )}
                      </div>

                      {/* Configuration hints */}
                      <div className="mt-4">
                        <WorkspaceConfigDescription
                          workspaceName={workspaceName}
                          config={originalConfig}
                          enabledClouds={workspaceStats.clouds}
                        />
                      </div>

                      {/* Detailed allowed users for private workspaces */}
                      <DetailedAllowedUsers
                        workspaceConfig={originalConfig}
                        allUsers={allUsers}
                      />
                    </div>
                  </Card>
                </div>
              )}

              {/* Right column - YAML Editor */}
              <div
                className={isNewWorkspace ? 'lg:col-span-3' : 'lg:col-span-2'}
              >
                <Card className="h-full flex flex-col">
                  <CardHeader>
                    <CardTitle className="text-base font-normal">
                      {isNewWorkspace
                        ? 'New Workspace YAML'
                        : 'Edit Workspace YAML'}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="flex-1 flex flex-col">
                    <div className="space-y-4 flex-1 flex flex-col">
                      {yamlError && (
                        <ErrorDisplay
                          error={yamlError}
                          onDismiss={() => setYamlError(null)}
                        />
                      )}
                      <div className="flex-1 flex flex-col">
                        <p className="text-sm text-gray-600 mb-3">
                          Configure infra-specific settings for this workspace.
                          Leave empty to use all accessible infrastructure.
                          Refer to{' '}
                          <a
                            href="https://docs.skypilot.co/en/latest/admin/workspaces.html#configuration"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600"
                          >
                            SkyPilot Docs
                          </a>{' '}
                          for more details.
                        </p>

                        {/* Example Configuration Section */}
                        <div className="mb-4">
                          <h4 className="text-sm font-medium text-gray-700 mb-2">
                            Example configuration:
                          </h4>
                          <div className="p-3 bg-gray-50 border rounded-lg">
                            <pre className="text-xs font-mono text-gray-600 whitespace-pre-wrap">
                              {`${workspaceName || 'my-workspace'}:
  private: true
  allowed_users:
  - user1@mydomain.com
  - user2@mydomain.com
  gcp:
    project_id: xxx
    disabled: false
  kubernetes:
    allowed_contexts:
    - context-1`}
                            </pre>
                          </div>
                        </div>

                        <Textarea
                          value={yamlValue}
                          onChange={(e) => handleYamlChange(e.target.value)}
                          className="font-mono text-sm flex-1 resize-none"
                          style={{ minHeight: '350px' }}
                          spellCheck={false}
                          placeholder={`# Enter workspace configuration in YAML format`}
                        />

                        {/* Action buttons */}
                        <div className="flex justify-end space-x-3 pt-3 border-gray-200">
                          <Button
                            onClick={handleSave}
                            disabled={saving || yamlError || loading}
                            className="inline-flex items-center bg-sky-600 hover:bg-sky-700 text-white"
                          >
                            <SaveIcon className="w-4 h-4 mr-1.5" />
                            {saving ? 'Applying...' : 'Apply'}
                          </Button>
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </div>
          </div>
        )}

        {/* Delete Confirmation Dialog */}
        <Dialog open={deleteState.showDialog} onOpenChange={handleCancelDelete}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader className="">
              <DialogTitle>Delete Workspace</DialogTitle>
              <DialogDescription>
                Are you sure you want to delete workspace &quot;
                {workspaceName}&quot;? This action cannot be undone.
              </DialogDescription>
            </DialogHeader>

            {/* Error Message Display */}
            {deleteState.error && (
              <ErrorDisplay
                error={deleteState.error}
                title="Deletion Failed"
                onDismiss={() =>
                  setDeleteState((prev) => ({ ...prev, error: null }))
                }
              />
            )}

            <DialogFooter className="">
              <Button
                variant="outline"
                onClick={handleCancelDelete}
                disabled={deleteState.deleting}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={handleConfirmDelete}
                disabled={deleteState.deleting}
              >
                {deleteState.deleting ? 'Deleting...' : 'Delete'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </>
    </>
  );
}
