#!/usr/bin/env sh
set -eu

: "${PYGEOAPI_CONFIG:=/app/pygeoapi-config.yml}"
: "${PYGEOAPI_OPENAPI:=/tmp/pygeoapi-openapi.yml}"

pygeoapi openapi generate "$PYGEOAPI_CONFIG" --output-file "$PYGEOAPI_OPENAPI"
exec pygeoapi serve --flask
