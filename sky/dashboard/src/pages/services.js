import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const Services = dynamic(
  () => import('@/components/services').then((mod) => mod.Services),
  { ssr: false }
);

export default function ServicesPage() {
  return (
    <>
      <Head>
        <title>Services | SkyPilot Dashboard</title>
      </Head>
      <Services />
    </>
  );
}
