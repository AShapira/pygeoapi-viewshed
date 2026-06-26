ARG BASE_IMAGE=busybox:1.37.0-musl
FROM ${BASE_IMAGE}

ARG APP_VERSION=0.1.0
LABEL org.opencontainers.image.title="pygeoapi-viewshed-data" \
      org.opencontainers.image.description="Seed image for the central-Israel SRTM COG used by pygeoapi-viewshed" \
      org.opencontainers.image.source="https://github.com/AShapira/pygeoapi-viewshed" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.licenses="NOASSERTION"

COPY srtm_center_israel_utm36.tif /seed/srtm_center_israel_utm36.tif
COPY seed-srtm.sh /seed/seed-srtm.sh
RUN chmod +x /seed/seed-srtm.sh

ENTRYPOINT ["/seed/seed-srtm.sh"]
