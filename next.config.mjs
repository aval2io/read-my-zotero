const backend = process.env.READ_MY_ZOTERO_BACKEND || 'http://127.0.0.1:8765';

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${backend}/api/:path*` }];
  }
};

export default nextConfig;
