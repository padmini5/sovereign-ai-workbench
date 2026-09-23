# Frontend: Vite production build served by nginx (non-root, port 8080).
# The app calls same-origin relative /api + /ws, so nginx reverse-proxies
# them to the backend — no API URL is baked in, no dev server in production.
FROM node:20-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html phase1.html vite.config.js ./
COPY public ./public
COPY src ./src
# SIH demo default: demo accounts stay visible in the bundle.
# (deploy.sh / compose build inherit this; pass --build-arg
# VITE_DEMO_CREDS=0 for a locked-down build that strips them.)
ARG VITE_DEMO_CREDS=1
ENV VITE_DEMO_CREDS=${VITE_DEMO_CREDS}
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
# Non-root: nginx user + port 8080 (unprivileged).
# The pid file must also live on a writable path (image default /run/nginx.pid
# is root-owned at runtime, which crash-loops the container as nginx user).
RUN sed -i 's/listen  *80;/listen 8080;/' /etc/nginx/conf.d/default.conf 2>/dev/null || true \
    && sed -i 's|^pid .*|pid /var/cache/nginx/nginx.pid;|' /etc/nginx/nginx.conf \
    && chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/run \
    && chmod -R u+rwX /var/cache/nginx /var/run
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/phase1.html > /dev/null || exit 1
