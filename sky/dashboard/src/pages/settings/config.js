import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';
import { PluginSlot } from '@/plugins/PluginSlot';

const Config = dynamic(
  () => import('@/components/config').then((mod) => mod.Config),
  { ssr: false }
);

export default function SettingsConfigPage() {
  return (
    <>
      <Head>
        <title>Settings | SkyPilot Dashboard</title>
      </Head>
      <div className="flex gap-6">
        <PluginSlot
          name="settings.sidebar"
          context={{ activeSection: 'config' }}
        />
        <div className="flex-1 min-w-0">
          <Config />
        </div>
      </div>
    </>
  );
}
