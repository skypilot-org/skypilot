import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const Users = dynamic(
  () => import('@/components/users').then((mod) => mod.Users),
  { ssr: false }
);

export default function UsersPage() {
  return (
    <>
      <Head>
        <title>Users | SkyPilot Dashboard</title>
      </Head>
      <Users />
    </>
  );
}
