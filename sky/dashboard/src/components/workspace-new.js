'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import dynamic from 'next/dynamic';
import { getWorkspaces } from '@/data/connectors/workspaces';
import {
  getWorkspaceName,
  workspaceNameError,
} from '@/hooks/useWorkspacesConfig';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const WorkspaceEditor = dynamic(
  () =>
    import('@/components/workspace-editor').then((mod) => mod.WorkspaceEditor),
  { ssr: false }
);

export function NewWorkspace() {
  const router = useRouter();
  const [workspaceName, setWorkspaceName] = useState('');
  const [showEditor, setShowEditor] = useState(false);
  const [existingWorkspaces, setExistingWorkspaces] = useState({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchExistingWorkspaces();
  }, []);

  const fetchExistingWorkspaces = async () => {
    try {
      const workspaces = await getWorkspaces();
      setExistingWorkspaces(workspaces);
    } catch (err) {
      console.error('Failed to fetch existing workspaces:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleNext = () => {
    if (isFormValid) {
      setShowEditor(true);
    }
  };

  const typedName = workspaceName.trim();
  const nameError = typedName ? workspaceNameError(typedName) : null;
  // Names are unique among current names; keys are ids.
  const takenById = Object.keys(existingWorkspaces).find(
    (id) => getWorkspaceName(existingWorkspaces, id) === typedName
  );
  const isWorkspaceNameTaken = Boolean(typedName) && takenById !== undefined;
  // A renamed workspace's original name (its id) is free to reuse, but
  // configs still typing it will reach the new workspace.
  const formerName =
    typedName &&
    !isWorkspaceNameTaken &&
    existingWorkspaces.hasOwnProperty(typedName)
      ? getWorkspaceName(existingWorkspaces, typedName)
      : null;
  const isFormValid = typedName && !nameError && !isWorkspaceNameTaken;

  if (showEditor) {
    return (
      <WorkspaceEditor workspaceName={workspaceName} isNewWorkspace={true} />
    );
  }

  return (
    <>
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base flex items-center">
          <Link href="/workspaces" className="text-sky-blue hover:underline">
            Workspaces
          </Link>
          <span className="mx-2 text-gray-500">›</span>
          <span className="text-sky-blue">New Workspace</span>
        </div>
      </div>

      <Card className="max-w-md">
        <CardHeader>
          <CardTitle className="text-base font-normal">
            Create New Workspace
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="workspace-name" className="text-sm font-normal">
              Workspace name
            </Label>
            <Input
              id="workspace-name"
              value={workspaceName}
              onChange={(e) => setWorkspaceName(e.target.value)}
              placeholder="Enter workspace name"
              autoFocus
              onKeyPress={(e) => {
                if (e.key === 'Enter' && isFormValid) {
                  handleNext();
                }
              }}
            />
            {nameError ? (
              <p className="text-sm text-red-600 mt-1">{nameError}</p>
            ) : isWorkspaceNameTaken ? (
              <p className="text-sm text-gray-500 mt-1">
                Workspace &quot;{workspaceName}&quot; already exists.{' '}
                <Link
                  href={`/workspaces/${takenById}`}
                  className="text-blue-600 hover:underline"
                >
                  View the workspace
                </Link>
              </p>
            ) : formerName ? (
              <p className="text-sm text-amber-700 mt-1">
                &quot;{typedName}&quot; was the original name of workspace
                &quot;{formerName}&quot;. Configs and scripts that still use
                &quot;{typedName}&quot; will reach this new workspace.
              </p>
            ) : (
              <p className="text-sm text-gray-500 mt-1">
                Choose a unique name for your workspace
              </p>
            )}
          </div>
          <Button
            onClick={handleNext}
            disabled={!isFormValid || loading}
            className="w-full bg-sky-600 hover:bg-sky-700 text-white disabled:bg-gray-300 disabled:text-gray-500"
          >
            {loading ? 'Loading...' : 'Next: Configure Workspace'}
          </Button>
        </CardContent>
      </Card>
    </>
  );
}
