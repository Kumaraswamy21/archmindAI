import type { NextConfig } from "next";

const deployEnvironment = process.env.ARCHMIND_DEPLOY_ENV ?? "development";
if (!["development", "test", "production"].includes(deployEnvironment)) {
  throw new Error("ARCHMIND_DEPLOY_ENV must be development, test, or production.");
}
if (deployEnvironment === "production" && !process.env.ARCHMIND_API_URL) {
  throw new Error("Production requires ARCHMIND_API_URL.");
}
const configuredApiOrigin = process.env.ARCHMIND_API_URL ?? "http://127.0.0.1:8000";
let apiUrl: URL;
try {
  apiUrl = new URL(configuredApiOrigin);
} catch {
  throw new Error("ARCHMIND_API_URL must be an absolute HTTP(S) origin.");
}
if (!["http:", "https:"].includes(apiUrl.protocol) || apiUrl.username || apiUrl.password ||
    apiUrl.pathname !== "/" || apiUrl.search || apiUrl.hash) {
  throw new Error("ARCHMIND_API_URL must be an HTTP(S) origin without credentials or a path.");
}
if (deployEnvironment === "production" && apiUrl.protocol !== "https:" &&
    !["127.0.0.1", "localhost", "::1"].includes(apiUrl.hostname)) {
  throw new Error("Production requires HTTPS for a nonlocal API origin.");
}
const apiOrigin = apiUrl.origin;

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` }];
  },
};

export default nextConfig;
