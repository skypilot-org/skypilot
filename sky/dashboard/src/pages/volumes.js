import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const Volumes = dynamic(
  () => import('@/components/volumes').then((mod) => mod.Volumes),
  { ssr: false }
);

export default function VolumesPage() {
  return (
    <>
      <Head>
        <title>Volumes | SkyPilot Dashboard</title>
      </Head>
      <Volumes />
    </>
  );
}
