'use client';

import React, { useEffect, useMemo, useState } from 'react';
import PropTypes from 'prop-types';
import { CircularProgress } from '@mui/material';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { batchUpdateUserRoles } from '@/data/connectors/users';
import {
  batchAddUsersToWorkspaces,
  batchRemoveUsersFromWorkspaces,
  getWorkspaces,
} from '@/data/connectors/workspaces';

/**
 * Renders the per-item result summary returned by the batch endpoints.
 * Both the user-role batch and the workspace batch endpoints return the
 * shape { succeeded: [...], failed: [{ <key>, error }] }.
 */
function ResultSummary({ result, idKey, idLabel }) {
  if (!result) return null;
  const succeeded = result.succeeded || [];
  const failed = result.failed || [];
  return (
    <div className="text-sm space-y-2">
      <div>
        <span className="font-medium text-green-700">
          {succeeded.length} succeeded
        </span>
        {failed.length > 0 && (
          <>
            {', '}
            <span className="font-medium text-red-700">
              {failed.length} failed
            </span>
          </>
        )}
        .
      </div>
      {failed.length > 0 && (
        <div className="border border-red-200 rounded p-2 bg-red-50 max-h-48 overflow-y-auto">
          <div className="text-xs font-medium text-red-700 mb-1">Failures:</div>
          <ul className="list-disc list-inside space-y-1">
            {failed.map((f, i) => (
              <li key={i} className="text-xs text-red-700">
                <span className="font-mono">
                  {idLabel}={f[idKey]}
                </span>
                : {f.error}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

ResultSummary.propTypes = {
  result: PropTypes.object,
  idKey: PropTypes.string.isRequired,
  idLabel: PropTypes.string.isRequired,
};

export function BatchRoleDialog({
  open,
  onClose,
  selectedUsers, // [{ userId, usernameDisplay, role }]
  onCompleted,
}) {
  const [role, setRole] = useState('user');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open) {
      setRole('user');
      setSubmitting(false);
      setResult(null);
      setError(null);
    }
  }, [open]);

  const handleApply = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const userIds = selectedUsers.map((u) => u.userId);
      const res = await batchUpdateUserRoles(userIds, role);
      setResult(res);
      onCompleted?.(res);
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const allDoneSuccessfully =
    result && (!result.failed || result.failed.length === 0);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && !submitting) onClose(allDoneSuccessfully);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            Change role for {selectedUsers.length} user(s)
          </DialogTitle>
          <DialogDescription>
            Apply the selected role to all selected users.
          </DialogDescription>
        </DialogHeader>

        {!result && (
          <>
            <div className="space-y-3 py-2">
              <div>
                <label
                  htmlFor="batch-role-select"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  New role
                </label>
                <select
                  id="batch-role-select"
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  className="block w-full p-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-sky-blue focus:border-sky-blue text-sm"
                  disabled={submitting}
                >
                  <option value="admin">Admin</option>
                  <option value="user">User</option>
                </select>
              </div>
              <div className="text-xs text-gray-500 border border-gray-200 rounded p-2 max-h-32 overflow-y-auto">
                <div className="font-medium text-gray-700 mb-1">
                  Affected users
                </div>
                {selectedUsers.map((u) => (
                  <div key={u.userId} className="truncate">
                    {u.usernameDisplay || u.userId}
                    <span className="text-gray-400"> ({u.role || '-'})</span>
                  </div>
                ))}
              </div>
              {error && <div className="text-sm text-red-600">{error}</div>}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => onClose(false)}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button onClick={handleApply} disabled={submitting}>
                {submitting ? (
                  <>
                    <CircularProgress size={14} className="mr-2" /> Applying...
                  </>
                ) : (
                  'Apply'
                )}
              </Button>
            </DialogFooter>
          </>
        )}

        {result && (
          <>
            <ResultSummary result={result} idKey="user_id" idLabel="user_id" />
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => onClose(allDoneSuccessfully)}
              >
                Close
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

BatchRoleDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  selectedUsers: PropTypes.array.isRequired,
  onCompleted: PropTypes.func,
};

function WorkspacePickerDialog({
  open,
  onClose,
  title,
  description,
  applyLabel,
  selectedUsers,
  variant, // 'add' | 'remove'
  onCompleted,
}) {
  const [workspaces, setWorkspaces] = useState({});
  const [loadingWorkspaces, setLoadingWorkspaces] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [picked, setPicked] = useState(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    setPicked(new Set());
    setSubmitting(false);
    setResult(null);
    setError(null);
    setLoadingWorkspaces(true);
    setLoadError(null);
    getWorkspaces()
      .then((data) => {
        setWorkspaces(data || {});
      })
      .catch((e) => {
        setLoadError(e?.message || String(e));
      })
      .finally(() => setLoadingWorkspaces(false));
  }, [open]);

  const privateWorkspaces = useMemo(() => {
    return Object.entries(workspaces)
      .filter(([, cfg]) => cfg && cfg.private)
      .map(([name, cfg]) => ({ name, config: cfg }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [workspaces]);

  const userIdSet = useMemo(
    () => new Set(selectedUsers.map((u) => u.userId)),
    [selectedUsers]
  );
  const usernameSet = useMemo(
    () => new Set(selectedUsers.map((u) => u.username).filter(Boolean)),
    [selectedUsers]
  );

  // For the 'remove' variant: show which selected users are currently in
  // each workspace's allowed_users so admins can see what will change.
  const computeCurrentMembers = (config) => {
    const allowed = config.allowed_users || [];
    return selectedUsers.filter(
      (u) => allowed.includes(u.userId) || allowed.includes(u.username)
    );
  };

  const togglePick = (name) => {
    const next = new Set(picked);
    if (next.has(name)) {
      next.delete(name);
    } else {
      next.add(name);
    }
    setPicked(next);
  };

  const handleApply = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const workspaceNames = Array.from(picked);
      const userIds = Array.from(userIdSet);
      let res;
      if (variant === 'add') {
        res = await batchAddUsersToWorkspaces(workspaceNames, userIds);
      } else {
        res = await batchRemoveUsersFromWorkspaces(workspaceNames, userIds);
      }
      setResult(res);
      onCompleted?.(res);
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const allDoneSuccessfully =
    result && (!result.failed || result.failed.length === 0);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && !submitting) onClose(allDoneSuccessfully);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {!result && (
          <>
            <div className="space-y-3 py-2">
              {loadingWorkspaces && (
                <div className="flex items-center text-sm text-gray-500">
                  <CircularProgress size={14} className="mr-2" />
                  Loading workspaces...
                </div>
              )}
              {loadError && (
                <div className="text-sm text-red-600">{loadError}</div>
              )}
              {!loadingWorkspaces && !loadError && (
                <>
                  {privateWorkspaces.length === 0 ? (
                    <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                      There are no private workspaces. Allowed users only apply
                      to private workspaces.
                    </div>
                  ) : (
                    <div className="border border-gray-200 rounded max-h-64 overflow-y-auto">
                      {privateWorkspaces.map(({ name, config }) => {
                        const isChecked = picked.has(name);
                        const currentMembers =
                          variant === 'remove'
                            ? computeCurrentMembers(config)
                            : null;
                        return (
                          <label
                            key={name}
                            className="flex items-start gap-2 p-2 hover:bg-gray-50 border-b border-gray-100 last:border-b-0 cursor-pointer"
                          >
                            <input
                              type="checkbox"
                              className="mt-1"
                              checked={isChecked}
                              onChange={() => togglePick(name)}
                              disabled={submitting}
                            />
                            <div className="flex-1 min-w-0">
                              <div className="text-sm font-medium text-gray-700 truncate">
                                {name}
                              </div>
                              {variant === 'remove' && (
                                <div className="text-xs text-gray-500 mt-0.5">
                                  {currentMembers.length === 0
                                    ? 'No selected user is in this workspace (no-op).'
                                    : `Will remove: ${currentMembers
                                        .map(
                                          (u) => u.usernameDisplay || u.userId
                                        )
                                        .join(', ')}`}
                                </div>
                              )}
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </>
              )}
              <div className="text-xs text-gray-500 border border-gray-200 rounded p-2 max-h-32 overflow-y-auto">
                <div className="font-medium text-gray-700 mb-1">
                  Selected users ({selectedUsers.length})
                </div>
                {selectedUsers.map((u) => (
                  <div key={u.userId} className="truncate">
                    {u.usernameDisplay || u.userId}
                  </div>
                ))}
              </div>
              {error && <div className="text-sm text-red-600">{error}</div>}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => onClose(false)}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button
                onClick={handleApply}
                disabled={submitting || picked.size === 0}
              >
                {submitting ? (
                  <>
                    <CircularProgress size={14} className="mr-2" /> Applying...
                  </>
                ) : (
                  applyLabel
                )}
              </Button>
            </DialogFooter>
          </>
        )}

        {result && (
          <>
            <ResultSummary
              result={result}
              idKey="workspace_name"
              idLabel="workspace"
            />
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => onClose(allDoneSuccessfully)}
              >
                Close
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

WorkspacePickerDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  applyLabel: PropTypes.string.isRequired,
  selectedUsers: PropTypes.array.isRequired,
  variant: PropTypes.oneOf(['add', 'remove']).isRequired,
  onCompleted: PropTypes.func,
};

export function BatchAddToWorkspacesDialog(props) {
  return (
    <WorkspacePickerDialog
      {...props}
      title={`Add ${props.selectedUsers.length} user(s) to workspaces`}
      description="Pick the private workspaces to add the selected users to. Only private workspaces are listed because allowed_users has no effect on public workspaces."
      applyLabel="Add"
      variant="add"
    />
  );
}

BatchAddToWorkspacesDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  selectedUsers: PropTypes.array.isRequired,
};

export function BatchRemoveFromWorkspacesDialog(props) {
  return (
    <WorkspacePickerDialog
      {...props}
      title={`Remove ${props.selectedUsers.length} user(s) from workspaces`}
      description="Pick the private workspaces to remove the selected users from. Removal is blocked if a user has active resources in that workspace."
      applyLabel="Remove"
      variant="remove"
    />
  );
}

BatchRemoveFromWorkspacesDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  selectedUsers: PropTypes.array.isRequired,
};
