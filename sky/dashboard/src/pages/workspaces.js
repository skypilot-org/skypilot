import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const Layout = dynamic(
  () => import('@/components/elements/layout').then((mod) => mod.Layout),
  { ssr: false }
);
const Workspaces = dynamic(
  () => import('@/components/workspaces').then((mod) => mod.Workspaces),
  { ssr: false }
);

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
