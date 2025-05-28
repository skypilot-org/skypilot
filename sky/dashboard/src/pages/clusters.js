import React from 'react';
import Head from 'next/head';
import { Clusters } from '@/components/clusters';

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
