import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const Config = dynamic(
  () => import('@/components/config').then((mod) => mod.Config),
  { ssr: false }
);

export default function ConfigPage() {
  return (
    <>
      <Head>
        <title>SkyPilot API Server | SkyPilot Dashboard</title>
      </Head>
      <Config />
    </>
  );
}
