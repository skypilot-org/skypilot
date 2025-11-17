import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

// TODO: how to add new routes in plugins?
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
      <Workspaces />
    </>
  );
}
