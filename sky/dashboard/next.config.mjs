/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/dashboard',
  output: 'export',
  images: {
    unoptimized: true,
  },
  env: {
    SKYPILOT_API_SERVER_ENDPOINT: process.env.SKYPILOT_API_SERVER_ENDPOINT,
    INFRA_CACHE_DURATION_MINUTES:
      process.env.INFRA_CACHE_DURATION_MINUTES || '10',
    INFRA_CACHE_DEBUG: process.env.INFRA_CACHE_DEBUG || 'false',
  },
};

export default nextConfig;
