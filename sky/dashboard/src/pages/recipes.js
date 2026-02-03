import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';

const RecipeHub = dynamic(
  () => import('@/components/recipe-hub').then((mod) => mod.RecipeHub),
  { ssr: false }
);

export default function RecipesPage() {
  return (
    <>
      <Head>
        <title>Recipes | SkyPilot Dashboard</title>
      </Head>
      <RecipeHub />
    </>
  );
}
