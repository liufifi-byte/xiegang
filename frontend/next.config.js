/** @type {import('next').NextConfig} */
const nextConfig = {
  // Keep local preview artifacts separate from production builds. Running a
  // build must never invalidate a live meeting preview.
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  async rewrites() {
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";
    return [{
      source: "/api/:path*",
      destination: `${apiBase}/api/:path*`,
    }];
  },
};

module.exports = nextConfig;
