#!/bin/sh
set -eu

mkdir -p /data/dem
cp /seed/srtm_center_israel_utm36.tif /data/dem/srtm_center_israel_utm36.tif
chmod 0644 /data/dem/srtm_center_israel_utm36.tif
echo "OK: seeded /data/dem/srtm_center_israel_utm36.tif"
