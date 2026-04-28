import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  basePath: '/admin',
  experimental: { serverComponentsExternalPackages: ['better-sqlite3'] },
};

export default nextConfig;
