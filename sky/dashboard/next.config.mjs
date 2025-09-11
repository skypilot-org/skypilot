/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: process.env.NEXT_BASE_PATH || '/dashboard',
  output: 'export',
  images: {
    unoptimized: true,
  },
  env: {
    SKYPILOT_API_SERVER_ENDPOINT: process.env.SKYPILOT_API_SERVER_ENDPOINT,
    SKYPILOT_RELEASE_NAME: process.env.SKYPILOT_RELEASE_NAME,
    INFRA_CACHE_DURATION_MINUTES:
      process.env.INFRA_CACHE_DURATION_MINUTES || '10',
    INFRA_CACHE_DEBUG: process.env.INFRA_CACHE_DEBUG || 'false',
  },
};

export default nextConfig;
