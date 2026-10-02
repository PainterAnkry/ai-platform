# ---------- Frontend ----------
# Multi-stage: build with Node, serve with nginx.

FROM node:20-alpine AS build

WORKDIR /workspace

# Install deps first for better layer caching.
COPY package.json ./
# We don't ship a package-lock.json in the scaffold; install with npm install.
# CI environments should swap in `npm ci` once a lockfile is committed.
RUN npm install --no-audit --no-fund

COPY tsconfig.json vite.config.ts ./
COPY index.html ./
COPY public ./public
COPY src ./src

RUN npm run build

# ---------- Runtime ----------
FROM nginx:1.27-alpine

# Copy the built bundle and a hardened nginx config.
COPY --from=build /workspace/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]