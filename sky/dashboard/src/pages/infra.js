import React from 'react';
import { GPUs } from '@/components/infra';
import Head from 'next/head';

export default function InfraPage() {
  return (
    <>
      <Head>
        <title>Infrastructure | SkyPilot Dashboard</title>
      </Head>
      <GPUs />
    </>
  );
} 
