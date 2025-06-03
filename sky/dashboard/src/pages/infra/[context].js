import React from 'react';
import { GPUs } from '@/components/infra';
import Head from 'next/head';

export default function InfraContextPage() {
  return (
    <>
      <Head>
        <title>Infra | SkyPilot Dashboard</title>
      </Head>
      <GPUs />
    </>
  );
}
