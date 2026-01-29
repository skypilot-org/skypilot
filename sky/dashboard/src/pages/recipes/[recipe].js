import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const RecipeDetail = dynamic(
  () => import('@/components/recipe-detail').then((mod) => mod.RecipeDetail),
  { ssr: false }
);

export default function RecipeDetailPage() {
  return (
    <>
      <Head>
        <title>Recipe | SkyPilot Dashboard</title>
      </Head>
      <RecipeDetail />
    </>
  );
}
