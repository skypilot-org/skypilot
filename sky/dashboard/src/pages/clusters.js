import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const Clusters = dynamic(
  () => import('@/components/clusters').then((mod) => mod.Clusters),
  { ssr: false }
);

export default function ClustersPage() {
  return (
    <>
      <Head>
        <title>Clusters | SkyPilot Dashboard</title>
      </Head>
      <Clusters />
    </>
  );
}
