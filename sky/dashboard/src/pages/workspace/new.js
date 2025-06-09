import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const Layout = dynamic(
  () => import('@/components/elements/layout').then((mod) => mod.Layout),
  { ssr: false }
);
const NewWorkspace = dynamic(
  () => import('@/components/workspace-new').then((mod) => mod.NewWorkspace),
  { ssr: false }
);

export default function NewWorkspacePage() {
  return (
    <>
      <Head>
        <title>New Workspace | SkyPilot Dashboard</title>
      </Head>
      <Layout highlighted="workspaces">
        <NewWorkspace />
      </Layout>
    </>
  );
}
