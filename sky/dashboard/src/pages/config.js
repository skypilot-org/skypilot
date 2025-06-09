import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const Layout = dynamic(
  () => import('@/components/elements/layout').then((mod) => mod.Layout),
  { ssr: false }
);
const Config = dynamic(
  () => import('@/components/config').then((mod) => mod.Config),
  { ssr: false }
);

export default function ConfigPage() {
  return (
    <>
      <Head>
        <title>Edit SkyPilot Configuration | SkyPilot Dashboard</title>
      </Head>
      <Layout highlighted="config">
        <Config />
      </Layout>
    </>
  );
}
