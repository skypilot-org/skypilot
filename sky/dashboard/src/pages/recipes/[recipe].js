import React from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';
import { useRouter } from 'next/router';

const RecipeDetail = dynamic(
  () => import('@/components/recipe-detail').then((mod) => mod.RecipeDetail),
  { ssr: false }
);

export default function RecipeDetailPage() {
  const router = useRouter();
  const { recipe } = router.query;

  const title = recipe
    ? `${recipe} | Recipes | SkyPilot Dashboard`
    : 'Recipes | SkyPilot Dashboard';

  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <RecipeDetail />
    </>
  );
}
