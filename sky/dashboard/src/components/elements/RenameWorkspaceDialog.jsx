'use client';

import React, { useState } from 'react';
import { PencilIcon } from 'lucide-react';
import { getClusters } from '@/data/connectors/clusters';
import { getManagedJobs } from '@/data/connectors/jobs';
import { updateWorkspace } from '@/data/connectors/workspaces';
import dashboardCache from '@/lib/cache';
import {
  invalidateWorkspaceCaches,
  workspaceNameError,
  workspaceNameTaken,
  workspaceNameTakenMessage,
} from '@/hooks/useWorkspacesConfig';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';

// The pencil that opens the rename dialog, shown next to a workspace's name.
export function RenameWorkspaceButton({
  onClick,
  disabled = false,
  title = 'Rename workspace',
  className,
}) {
  return (
    <button
      type="button"
      aria-label="Rename workspace"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex items-center p-1 rounded text-gray-400 hover:text-sky-blue focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-sky-blue disabled:text-gray-300 disabled:cursor-not-allowed',
        className
      )}
    >
      <PencilIcon className="w-3.5 h-3.5" />
    </button>
  );
}

// Mount it to open it. `config` is the workspace's stored config as last read
// (sent as `expected_config`); `workspacesConfig` is all workspaces, for the
// name-taken check. `onRenamed(newName, renamedConfig)` runs after the write.
export function RenameWorkspaceDialog({
  workspaceId,
  config,
  workspacesConfig,
  onClose,
  onRenamed,
}) {
  // Server-computed flags are not part of the stored config.
  const { writable, read_only: readOnly, ...storedConfig } = config || {};
  const currentName = storedConfig.name || workspaceId;
  const [value, setValue] = useState(currentName);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const canSubmit = !saving && value.trim() !== currentName;

  const handleRename = async () => {
    const newName = value.trim();
    const taken = workspaceNameTaken(workspacesConfig, newName, workspaceId);
    const nameError =
      workspaceNameError(newName) ||
      (taken && workspaceNameTakenMessage(workspacesConfig, newName, taken));
    if (nameError) {
      setError(nameError);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      // Only `name` changes, so the server allows it while clusters and jobs
      // are running.
      const renamed = { ...storedConfig, name: newName };
      await updateWorkspace(workspaceId, renamed, storedConfig);
      invalidateWorkspaceCaches();
      // Cluster and job records carry the workspace name.
      dashboardCache.invalidate(getClusters);
      dashboardCache.invalidateFunction(getManagedJobs);
      onRenamed(newName, renamed);
    } catch (err) {
      setSaving(false);
      setError(
        err?.type === 'WorkspaceConfigConflictError'
          ? 'The workspace changed since this page loaded. Refresh and try again.'
          : err?.message || String(err)
      );
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Rename Workspace</DialogTitle>
        </DialogHeader>
        {/* DialogContent wraps its children in one div, so its gap does not
            space them out. */}
        <div className="space-y-2 py-4">
          <Label htmlFor="rename-workspace-name">Name</Label>
          <Input
            id="rename-workspace-name"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && canSubmit) handleRename();
            }}
            className="focus-visible:ring-1 focus-visible:ring-sky-blue focus-visible:ring-offset-0"
            autoFocus
          />
          <DialogDescription className="text-xs text-gray-500">
            {currentName === workspaceId
              ? `Scripts and configs that use "${currentName}" keep working.`
              : `Scripts and configs that use "${currentName}" will stop working, and another workspace can take this name.`}
          </DialogDescription>
          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            onClick={handleRename}
            disabled={!canSubmit}
            className="bg-sky-600 hover:bg-sky-700 text-white"
          >
            {saving ? 'Renaming...' : 'Rename'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
