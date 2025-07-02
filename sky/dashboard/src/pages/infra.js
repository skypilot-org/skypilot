import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const GPUs = dynamic(
  () => import('@/components/infra').then((mod) => mod.GPUs),
  { ssr: false }
);

export default function InfraPage() {
  return (
    <>
      <Head>
        <title>Infra | SkyPilot Dashboard</title>
      </Head>
      <GPUs />
    </>
  );
}
