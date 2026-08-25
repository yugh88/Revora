/** @type {import('next').NextConfig} */

/**
 * Where the FastAPI backend lives. Read at server start, not baked into the
 * browser bundle — see the rewrite below for why that matters.
 */
const BACKEND_ORIGIN = (
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
).replace(/\/+$/, '');

const nextConfig = {
  reactStrictMode: true,
  // Standalone output keeps the Docker image small: Next traces exactly the
  // node_modules it needs instead of shipping the whole tree.
  output: 'standalone',
  poweredByHeader: false,

  /**
   * Same-origin proxy to the backend.
   *
   * The browser calls /api/backend/* and Next forwards it server-side. This is
   * not an optimisation — it is the only way the dashboard works at all right
   * now: the backend (frozen since Session 5) installs no CORS middleware, so a
   * direct browser call from :3000 to :8000 is blocked before it is sent.
   *
   * Proxying is the frontend-only fix. It also happens to be the better shape
   * for docker-compose, where the API is reachable on the internal network but
   * not necessarily from the user's browser.
   *
   * If CORS is added to the backend in a later session, this can stay — it
   * costs one hop and removes a class of deployment problem.
   */
  async rewrites() {
    return [
      {
        source: '/api/backend/:path*',
        destination: `${BACKEND_ORIGIN}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
