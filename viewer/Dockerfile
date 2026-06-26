FROM node:22.14-alpine AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY index.html vite.config.js ./
COPY src ./src
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine
ARG APP_VERSION=0.1.0
LABEL org.opencontainers.image.title="pygeoapi-viewshed-viewer" \
      org.opencontainers.image.description="OpenLayers browser viewer and nginx gateway for pygeoapi-viewshed" \
      org.opencontainers.image.source="https://github.com/AShapira/pygeoapi-viewshed" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.licenses="NOASSERTION"

USER root
RUN apk upgrade --no-cache
USER 101

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
