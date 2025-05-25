import React from 'react';
import { useRouter } from 'next/router';
import { WorkspaceEditor } from '@/components/workspace-editor';

export default function WorkspacePage() {
  const router = useRouter();
  const { name } = router.query;

  // Show loading while router is not ready or name param is not available
  if (!router.isReady || !name) {
    return <div>Loading...</div>;
  }

  return <WorkspaceEditor workspaceName={name} />;
}
