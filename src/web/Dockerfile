# Build from the repository root: docker build -f src/web/Dockerfile -t rav26-web .
FROM node:22-alpine AS build
WORKDIR /app
COPY src/web/package.json src/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/web/ ./
ENV VITE_RAV_MODE=pilot
RUN npx tsc --noEmit && npm test && npm run build

FROM nginx:stable-alpine AS web
RUN apk add --no-cache jq
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/web/default.conf.template /etc/nginx/templates/default.conf.template
COPY deploy/web/25-public-config.sh /docker-entrypoint.d/25-public-config.sh
RUN chmod +x /docker-entrypoint.d/25-public-config.sh && \
    chown -R nginx:nginx /usr/share/nginx/html /etc/nginx/conf.d /var/cache/nginx /run
ENV PORT=8080 NGINX_ENVSUBST_FILTER=^PORT$
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${PORT}/health" || exit 1

