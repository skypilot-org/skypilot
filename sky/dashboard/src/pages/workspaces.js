import React from 'react';
import { Layout } from '@/components/elements/layout';
import { Workspaces } from '@/components/workspaces'; // This component will be created next

export default function WorkspacesPage() {
  return (
    <Layout highlighted="workspaces">
      <Workspaces />
    </Layout>
  );
} 
