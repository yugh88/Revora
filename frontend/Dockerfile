# Revora frontend. BUILD_SPEC Sections 3 and 12.
#
# Multi-stage so the runtime image carries the built app and nothing else — no
# source, no dev dependencies, no npm cache. Next's `output: 'standalone'`
# (next.config.js) traces the exact node_modules the server needs, which is the
# difference between a ~180MB image and a ~1GB one.

# --- deps: install once, cached on the lockfile ---------------------------- #
FROM node:22-alpine AS deps
WORKDIR /app
# Only the manifests, so this layer is reused on every build that does not
# change dependencies.
COPY package.json package-lock.json ./
RUN npm ci

# --- build ----------------------------------------------------------------- #
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* is inlined into the client bundle at BUILD time, not read at
# runtime, so the API URL has to be supplied here. It defaults to the local
# backend; override with --build-arg for another host.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# --- runtime --------------------------------------------------------------- #
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Run unprivileged. The app never needs root.
RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs

# public/ is not in the manifest and does not exist, so it is not copied.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

# server.js is emitted by the standalone build.
CMD ["node", "server.js"]
