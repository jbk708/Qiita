# syntax=docker/dockerfile:1

# qiita-web is a static SPA (adapter-static, svelte.config.js) — there is no
# Node runtime in prod. The image is therefore build-node / serve-nginx, and
# the nginx layer is what makes the same-origin contract hold: src/lib/api.ts
# fetches a relative `/api/v1/*`, so whatever serves the bundle must also
# proxy that prefix to the control plane or the browser hits CORS.

# ---- build ----------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app

# No `npm ci`: package-lock.json is gitignored (see .gitignore), so there is
# no lockfile in the build context to install from.
COPY package.json ./
RUN npm install --no-audit --no-fund

COPY . .

# Baked at build time, unlike the /api proxy target below. api.ts reads
# import.meta.env.VITE_QIITA_BASE for CP_ORIGIN, the full-page login redirect
# target, and that MUST be the real control-plane origin: /auth/login sets the
# freshness cookie on the CP's own domain and AuthRocket bounces back to the
# CP's /auth/handoff, which only sees the cookie if it was set there. Vite
# inlines it into the bundle, so it cannot be deferred to container start.
ARG VITE_QIITA_BASE
ENV VITE_QIITA_BASE=${VITE_QIITA_BASE}
RUN npm run build

# ---- serve ----------------------------------------------------------------
FROM nginx:1.27-alpine
COPY --from=build /app/build /usr/share/nginx/html
COPY docker/default.conf.template /etc/nginx/templates/default.conf.template

# The base image envsubsts /etc/nginx/templates/*.template into conf.d/ at
# start. Restrict substitution to QIITA_* so nginx's own runtime variables
# ($uri, $host, $remote_addr) survive into the generated config.
ENV NGINX_ENVSUBST_FILTER=^QIITA_ \
    QIITA_WEB_PORT=8081 \
    QIITA_CP_UPSTREAM=http://127.0.0.1:8080 \
    QIITA_CP_HOST=127.0.0.1:8080

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO /dev/null "http://127.0.0.1:${QIITA_WEB_PORT}/index.html" || exit 1
