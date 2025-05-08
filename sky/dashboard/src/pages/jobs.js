import Head from 'next/head';
import { ManagedJobs } from '@/components/jobs';

export default function JobsPage() {
  return (
    <>
      <Head>
        <title>Managed Jobs | SkyPilot Dashboard</title>
      </Head>
      <ManagedJobs />
    </>
  );
}
