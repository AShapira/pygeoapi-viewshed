import gzip
import os
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import requests
from osgeo import gdal


GEOSERVER_URL = os.environ.get("GEOSERVER_URL", "http://geoserver:8080/geoserver").rstrip("/")
GEOSERVER_USER = os.environ.get("GEOSERVER_USER", "admin")
GEOSERVER_PASSWORD = os.environ.get("GEOSERVER_PASSWORD", "geoserver-change-me")
SRTM_BASE_URL = os.environ.get("SRTM_BASE_URL", "https://s3.amazonaws.com/elevation-tiles-prod/skadi").rstrip("/")
SRTM_WORK_DIR = Path(os.environ.get("SRTM_WORK_DIR", "/data/work/srtm-center-israel"))
SRTM_PATH = Path(os.environ.get("SRTM_PATH", "/data/dem/srtm_center_israel_utm36.tif"))
AOI_BBOX = tuple(
    float(value)
    for value in os.environ.get("SRTM_AOI_BBOX", "34.55,31.45,35.65,32.55").split(",")
)
TILES = tuple(
    value.strip()
    for value in os.environ.get("SRTM_TILES", "N31E034,N31E035,N32E034,N32E035").split(",")
    if value.strip()
)
WORKSPACE = os.environ.get("SRTM_WORKSPACE", "dem")
LAYER_NAME = os.environ.get("SRTM_LAYER_NAME", "srtm_center_israel")


def main() -> None:
    if len(AOI_BBOX) != 4:
        raise RuntimeError("SRTM_AOI_BBOX must be minLon,minLat,maxLon,maxLat")
    wait_for_geoserver()
    create_srtm_dem()
    publish_srtm()
    print(f"OK: published {WORKSPACE}:{LAYER_NAME}")


def wait_for_geoserver(timeout: int = 240) -> None:
    session = requests.Session()
    session.auth = (GEOSERVER_USER, GEOSERVER_PASSWORD)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = session.get(f"{GEOSERVER_URL}/rest/about/version.json", timeout=10)
            if response.ok:
                return
        except requests.RequestException:
            pass
        time.sleep(3)
    raise RuntimeError("GeoServer did not become ready")


def create_srtm_dem() -> None:
    SRTM_WORK_DIR.mkdir(parents=True, exist_ok=True)
    SRTM_PATH.parent.mkdir(parents=True, exist_ok=True)
    force_rebuild = os.environ.get("SRTM_FORCE_REBUILD", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if SRTM_PATH.exists() and SRTM_PATH.stat().st_size > 0 and not force_rebuild:
        print(f"Using existing SRTM DEM: {SRTM_PATH}")
        return

    hgt_paths = [download_tile(tile) for tile in TILES]
    vrt_path = SRTM_WORK_DIR / "srtm-center-israel.vrt"
    warped_path = SRTM_WORK_DIR / "srtm-center-israel-utm36.tif"

    vrt = gdal.BuildVRT(str(vrt_path), [str(path) for path in hgt_paths])
    if vrt is None:
        raise RuntimeError("GDAL could not build SRTM VRT")
    vrt = None

    min_lon, min_lat, max_lon, max_lat = AOI_BBOX
    warp_options = gdal.WarpOptions(
        format="GTiff",
        dstSRS="EPSG:32636",
        outputBounds=(min_lon, min_lat, max_lon, max_lat),
        outputBoundsSRS="EPSG:4326",
        xRes=30,
        yRes=30,
        resampleAlg="bilinear",
        srcNodata=-32768,
        dstNodata=-32768,
        multithread=True,
        creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"],
    )
    warped = gdal.Warp(str(warped_path), str(vrt_path), options=warp_options)
    if warped is None:
        raise RuntimeError("GDAL could not warp SRTM DEM to EPSG:32636")
    warped = None

    translated = gdal.Translate(
        str(SRTM_PATH),
        str(warped_path),
        format="COG",
        creationOptions=["COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"],
    )
    if translated is None:
        raise RuntimeError("GDAL could not translate SRTM DEM to COG")
    translated = None


def download_tile(tile: str) -> Path:
    latitude_band = tile[:3]
    gz_path = SRTM_WORK_DIR / f"{tile}.hgt.gz"
    hgt_path = SRTM_WORK_DIR / f"{tile}.hgt"
    if not gz_path.exists():
        url = f"{SRTM_BASE_URL}/{latitude_band}/{tile}.hgt.gz"
        with requests.get(url, stream=True, timeout=120) as response:
            if not response.ok:
                raise RuntimeError(f"SRTM tile download failed: {url} {response.status_code}")
            with gz_path.open("wb") as target:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        target.write(chunk)
    if not hgt_path.exists() or hgt_path.stat().st_size == 0:
        with gzip.open(gz_path, "rb") as source, hgt_path.open("wb") as target:
            shutil.copyfileobj(source, target)
    return hgt_path


def publish_srtm() -> None:
    session = requests.Session()
    session.auth = (GEOSERVER_USER, GEOSERVER_PASSWORD)
    ensure_workspace(session, WORKSPACE)
    store = quote(LAYER_NAME, safe="")
    workspace = quote(WORKSPACE, safe="")
    store_path = f"/rest/workspaces/{workspace}/coveragestores/{store}"
    if exists(session, store_path + ".json"):
        return
    geoserver(
        session,
        "PUT",
        f"{store_path}/external.geotiff?configure=first&coverageName={store}",
        data=SRTM_PATH.as_posix(),
        headers={"Content-Type": "text/plain"},
    )


def ensure_workspace(session: requests.Session, workspace: str) -> None:
    encoded = quote(workspace, safe="")
    if not exists(session, f"/rest/workspaces/{encoded}.json"):
        geoserver(session, "POST", "/rest/workspaces", json={"workspace": {"name": workspace}})


def exists(session: requests.Session, path: str) -> bool:
    response = session.get(f"{GEOSERVER_URL}{path}", timeout=30)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


def geoserver(session: requests.Session, method: str, path: str, **kwargs) -> requests.Response:
    response = session.request(method, f"{GEOSERVER_URL}{path}", timeout=120, **kwargs)
    if not response.ok:
        raise RuntimeError(f"GeoServer {method} {path} failed: {response.status_code} {response.text[:500]}")
    return response


if __name__ == "__main__":
    main()
