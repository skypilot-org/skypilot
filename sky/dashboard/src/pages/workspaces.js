import React from 'react';
import Head from 'next/head';
import { Layout } from '@/components/elements/layout';
import { Workspaces } from '@/components/workspaces'; // This component will be created next

export default function WorkspacesPage() {
  return (
    <>
      <Head>
        <title>Workspaces | SkyPilot Dashboard</title>
      </Head>
      <Layout highlighted="workspaces">
        <Workspaces />
      </Layout>
    </>
  );
}
