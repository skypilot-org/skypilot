import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { WorkspaceEditor } from '@/components/workspace-editor';
import { getWorkspaces } from '@/data/connectors/workspaces';
import { Layout } from '@/components/elements/layout';
import Link from 'next/link';
import Head from 'next/head';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default function NewWorkspacePage() {
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
    if (workspaceName.trim() && !isWorkspaceNameTaken) {
      setShowEditor(true);
    }
  };

  const isWorkspaceNameTaken =
    workspaceName.trim() &&
    existingWorkspaces.hasOwnProperty(workspaceName.trim());
  const isFormValid = workspaceName.trim() && !isWorkspaceNameTaken;

  if (showEditor) {
    return (
      <WorkspaceEditor workspaceName={workspaceName} isNewWorkspace={true} />
    );
  }

  return (
    <>
      <Head>
        <title>Create New Workspace | SkyPilot Dashboard</title>
      </Head>
      <Layout highlighted="workspaces">
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
              {isWorkspaceNameTaken ? (
                <p className="text-sm text-gray-500 mt-1">
                  Workspace &quot;{workspaceName}&quot; already exists.{' '}
                  <Link
                    href={`/workspaces/${workspaceName}`}
                    className="text-blue-600 hover:underline"
                  >
                    View the workspace
                  </Link>
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
              className="w-full bg-blue-600 hover:bg-blue-700 text-white disabled:bg-gray-300 disabled:text-gray-500"
            >
              {loading ? 'Loading...' : 'Next: Configure Workspace'}
            </Button>
          </CardContent>
        </Card>
      </Layout>
    </>
  );
}
