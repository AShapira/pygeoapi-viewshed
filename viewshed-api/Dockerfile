ARG GDAL_IMAGE=ghcr.io/osgeo/gdal:ubuntu-small-3.11.4
FROM ${GDAL_IMAGE}

ARG APP_VERSION=0.1.0
LABEL org.opencontainers.image.title="pygeoapi-viewshed-api" \
      org.opencontainers.image.description="pygeoapi OGC API - Processes service for GDAL viewshed and GeoServer publication" \
      org.opencontainers.image.source="https://github.com/AShapira/pygeoapi-viewshed" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.licenses="NOASSERTION"

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends python3-pip python3-venv \
    && python3 -m venv --system-site-packages /opt/venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove python3-pip python3-setuptools python3-wheel \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

COPY pygeoapi-config.yml entrypoint.sh ./
COPY plugins ./plugins
COPY tools ./tools

RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /data/results /data/work \
    && chown -R 1000:1000 /app /data

USER 1000:1000
EXPOSE 5000
ENTRYPOINT ["/app/entrypoint.sh"]
