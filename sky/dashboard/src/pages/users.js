import React from 'react';
import Head from 'next/head';
import { Users } from '@/components/users';

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
