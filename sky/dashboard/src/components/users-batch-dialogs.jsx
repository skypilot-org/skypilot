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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { batchUpdateUserRoles } from '@/data/connectors/users';
import {
  batchAddUsersToWorkspaces,
  batchRemoveUsersFromWorkspaces,
  getWorkspaces,
} from '@/data/connectors/workspaces';
import dashboardCache from '@/lib/cache';

const FOOTER_BUTTON_CLASS = 'min-w-[80px]';

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
    <div className="space-y-3 py-2 text-sm">
      <div className="text-gray-700">
        <span className="font-medium text-green-600">
          {succeeded.length} succeeded
        </span>
        {failed.length > 0 && (
          <>
            {', '}
            <span className="font-medium text-red-600">
              {failed.length} failed
            </span>
          </>
        )}
        .
      </div>
      {failed.length > 0 && (
        <div className="border border-gray-200 rounded-md max-h-48 overflow-y-auto">
          <div className="px-3 py-2 text-xs font-medium text-gray-700 border-b border-gray-200 bg-gray-50">
            Failures
          </div>
          <ul className="divide-y divide-gray-100">
            {failed.map((f, i) => (
              <li key={i} className="px-3 py-2 text-xs text-gray-700">
                <span className="font-mono text-gray-500">
                  {idLabel}={f[idKey]}
                </span>
                {/* whitespace-pre-wrap renders the \n in server error
                    messages as actual line breaks. */}
                <span className="ml-2 text-red-600 whitespace-pre-wrap">
                  {f.error}
                </span>
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

function SelectedUsersPanel({ selectedUsers, includeRole = false }) {
  return (
    <div className="border border-gray-200 rounded-md">
      <div className="px-3 py-2 text-xs font-medium text-gray-700 border-b border-gray-200 bg-gray-50">
        {includeRole
          ? 'Affected users'
          : `Selected users (${selectedUsers.length})`}
      </div>
      <div className="max-h-32 overflow-y-auto divide-y divide-gray-100">
        {selectedUsers.map((u) => (
          <div
            key={u.userId}
            className="px-3 py-1.5 text-xs text-gray-700 truncate"
          >
            {u.usernameDisplay || u.userId}
            {includeRole && (
              <span className="text-gray-400"> ({u.role || '-'})</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

SelectedUsersPanel.propTypes = {
  selectedUsers: PropTypes.array.isRequired,
  includeRole: PropTypes.bool,
};

export function BatchRoleDialog({ open, onClose, selectedUsers }) {
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
            <div className="flex flex-col gap-4 py-2">
              <div className="grid gap-2">
                <label
                  htmlFor="batch-role-select"
                  className="text-sm font-medium text-gray-700"
                >
                  New role
                </label>
                <Select
                  value={role}
                  onValueChange={setRole}
                  disabled={submitting}
                >
                  <SelectTrigger
                    id="batch-role-select"
                    className="w-full focus:ring-0 focus:ring-offset-0"
                  >
                    <SelectValue placeholder="Select role" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">Admin</SelectItem>
                    <SelectItem value="user">User</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <SelectedUsersPanel selectedUsers={selectedUsers} includeRole />
              {error && <div className="text-sm text-red-600">{error}</div>}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => onClose(false)}
                disabled={submitting}
                className={FOOTER_BUTTON_CLASS}
              >
                Cancel
              </Button>
              <Button
                variant="default"
                onClick={handleApply}
                disabled={submitting}
                className={`bg-sky-600 text-white hover:bg-sky-700 ${FOOTER_BUTTON_CLASS}`}
              >
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
                className={FOOTER_BUTTON_CLASS}
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
};

function WorkspacePickerDialog({
  open,
  onClose,
  title,
  description,
  applyLabel,
  selectedUsers,
  variant, // 'add' | 'remove'
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
    let cancelled = false;
    setPicked(new Set());
    setSubmitting(false);
    setResult(null);
    setError(null);
    setLoadingWorkspaces(true);
    setLoadError(null);
    dashboardCache
      .get(getWorkspaces)
      .then((data) => {
        if (cancelled) return;
        setWorkspaces(data || {});
      })
      .catch((e) => {
        if (cancelled) return;
        console.error('Error fetching workspaces:', e);
        setLoadError('Unable to load workspaces. Please try again.');
      })
      .finally(() => {
        if (!cancelled) setLoadingWorkspaces(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const privateWorkspaces = useMemo(() => {
    return Object.entries(workspaces)
      .filter(([, cfg]) => cfg && cfg.private)
      .map(([name, cfg]) => ({ name, config: cfg }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [workspaces]);

  // Returns the subset of selectedUsers already listed in this
  // workspace's allowed_users. Used in both variants:
  //  - 'remove': these are the users that will actually be removed.
  //  - 'add':    these are the users for whom the add is a no-op
  //              (already in allowed_users).
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
      const userIds = selectedUsers.map((u) => u.userId);
      let res;
      if (variant === 'add') {
        res = await batchAddUsersToWorkspaces(workspaceNames, userIds);
      } else {
        res = await batchRemoveUsersFromWorkspaces(workspaceNames, userIds);
      }
      // Invalidate the workspaces cache so the next dialog open (or any
      // other workspace consumer) sees the new allowed_users instead of
      // a stale snapshot. Without this, opening Remove after an Add shows
      // "No selected user is in this workspace".
      dashboardCache.invalidate(getWorkspaces);
      setResult(res);
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
            <div className="flex flex-col gap-4 py-2">
              {loadingWorkspaces && (
                <div className="flex items-center text-sm text-gray-500">
                  <CircularProgress size={14} className="mr-2" />
                  Loading workspaces...
                </div>
              )}
              {!loadingWorkspaces && loadError && (
                <div className="text-sm text-red-600">{loadError}</div>
              )}
              {!loadingWorkspaces &&
                !loadError &&
                privateWorkspaces.length === 0 && (
                  <div className="text-sm text-gray-600 border border-gray-200 rounded-md px-3 py-2 bg-gray-50">
                    No private workspaces are configured. Allowed users only
                    apply to private workspaces.
                  </div>
                )}
              {!loadingWorkspaces &&
                !loadError &&
                privateWorkspaces.length > 0 && (
                  <div className="border border-gray-200 rounded-md max-h-64 overflow-y-auto">
                    {privateWorkspaces.map(({ name, config }) => {
                      const isChecked = picked.has(name);
                      const currentMembers = computeCurrentMembers(config);
                      // currentMembers is `selectedUsers.filter(...)` so the
                      // elements are the same references as in selectedUsers
                      // -- a direct .includes(u) gives us the complement.
                      const newMembers = selectedUsers.filter(
                        (u) => !currentMembers.includes(u)
                      );
                      const fmt = (us) =>
                        us.map((u) => u.usernameDisplay || u.userId).join(', ');
                      let addHint = null;
                      if (variant === 'add') {
                        if (newMembers.length === 0) {
                          addHint =
                            'All selected users are already in this workspace (no-op).';
                        } else if (currentMembers.length === 0) {
                          addHint = `Will add: ${fmt(newMembers)}`;
                        } else {
                          addHint = `Will add: ${fmt(newMembers)}. Already in: ${fmt(currentMembers)}`;
                        }
                      }
                      return (
                        <label
                          key={name}
                          className="flex items-start gap-3 px-3 py-2 hover:bg-gray-50 border-b border-gray-100 last:border-b-0 cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            className="mt-0.5"
                            checked={isChecked}
                            onChange={() => togglePick(name)}
                            disabled={submitting}
                          />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm text-gray-700 truncate">
                              {name}
                            </div>
                            {variant === 'remove' && (
                              <div className="text-xs text-gray-500 mt-0.5">
                                {currentMembers.length === 0
                                  ? 'No selected user is in this workspace (no-op).'
                                  : `Will remove: ${fmt(currentMembers)}`}
                              </div>
                            )}
                            {variant === 'add' && (
                              <div className="text-xs text-gray-500 mt-0.5">
                                {addHint}
                              </div>
                            )}
                          </div>
                        </label>
                      );
                    })}
                  </div>
                )}
              <SelectedUsersPanel selectedUsers={selectedUsers} />
              {error && <div className="text-sm text-red-600">{error}</div>}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => onClose(false)}
                disabled={submitting}
                className={FOOTER_BUTTON_CLASS}
              >
                Cancel
              </Button>
              {variant === 'remove' ? (
                <Button
                  variant="destructive"
                  onClick={handleApply}
                  disabled={submitting || picked.size === 0}
                  className={FOOTER_BUTTON_CLASS}
                >
                  {submitting ? (
                    <>
                      <CircularProgress size={14} className="mr-2" />{' '}
                      Removing...
                    </>
                  ) : (
                    applyLabel
                  )}
                </Button>
              ) : (
                <Button
                  variant="default"
                  onClick={handleApply}
                  disabled={submitting || picked.size === 0}
                  className={`bg-sky-600 text-white hover:bg-sky-700 ${FOOTER_BUTTON_CLASS}`}
                >
                  {submitting ? (
                    <>
                      <CircularProgress size={14} className="mr-2" /> Adding...
                    </>
                  ) : (
                    applyLabel
                  )}
                </Button>
              )}
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
                className={FOOTER_BUTTON_CLASS}
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
