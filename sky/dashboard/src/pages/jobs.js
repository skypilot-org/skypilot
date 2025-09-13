import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const ManagedJobs = dynamic(
  () => import('@/components/jobs').then((mod) => mod.ManagedJobs),
  { ssr: false }
);

export default function JobsPage() {
  return (
    <>
      <Head>
        <title>Managed Jobs | SkyPilot Dashboard</title>
      </Head>
      <ManagedJobs />
    </>
  );
}
