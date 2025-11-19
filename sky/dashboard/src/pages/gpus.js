import React from 'react';
import { GPUs } from '@/components/gpus';
import Head from 'next/head';

export default function GPUsPage() {
  return (
    <>
      <Head>
        <title>GPUs | SkyPilot Dashboard</title>
      </Head>
      <GPUs />
    </>
  );
}
