# Security Notes

This project is a demonstration deployment and should be reviewed before
production use. The release images are built with minimal custom layers, run the
API and viewer as non-root users, and avoid external runtime basemap/CDN
dependencies.

## 0.1.0 Docker Scout Snapshot

The following Docker Scout results were captured during the `0.1.0` release
validation on 2026-06-26.

| Image | Result |
| --- | --- |
| `ghcr.io/ashapira/pygeoapi-viewshed-api:0.1.0` | 0 critical, 1 high, 19 medium, 17 low |
| `ghcr.io/ashapira/pygeoapi-viewshed-viewer:0.1.0` | 0 critical, 2 high, 8 medium, 0 low |
| `ghcr.io/ashapira/pygeoapi-viewshed-data:0.1.0` | no vulnerable packages detected |
| `docker.osgeo.org/geoserver:3.0.0` | 0 critical, 2 high, 27 medium, 14 low |

The API image high finding is in GDAL 3.11.4. This release keeps
`ghcr.io/osgeo/gdal:ubuntu-small-3.11.4` because the service depends on GDAL CLI
and Python bindings. The production backlog should evaluate GDAL 3.13+ once the
OSGeo GDAL image tag and pygeoapi dependency set are validated together.

The viewer image is based on `nginxinc/nginx-unprivileged:1.29-alpine` and runs
`apk upgrade --no-cache` during build. Remaining findings are in Alpine base
packages where Docker Scout reported no fixed package version for the current
repository snapshot.

GeoServer is consumed as an upstream image, not built by this repository.
GeoServer findings are documented separately because they must be addressed by
upstream image refresh, replacement with an organization-owned GeoServer image,
or compensating runtime controls.

## Runtime Notes

- Replace the default GeoServer admin password for any non-local deployment.
- Keep the viewer behind trusted network boundaries or add an authentication
  proxy before exposing it.
- Keep `docker-compose.airgap.yml` pinned to immutable image tags or digests for
  controlled deployments.
- Re-run `scripts\Security-Check.ps1` whenever base image tags or dependency
  versions change.
