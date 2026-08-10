/** @type {import('next').NextConfig} */
const nextConfig = {
  // Keep local preview artifacts separate from production builds.
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",

  // 同源部署: 前端和后端在同一个域名下, /api/* 由 nginx 反代到 web 容器。
  // 本地开发仍用 rewrites 反代到 127.0.0.1:8001。
  async rewrites() {
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";
    return [{
      source: "/api/:path*",
      destination: `${apiBase}/api/:path*`,
    }];
  },
};

module.exports = nextConfig;
