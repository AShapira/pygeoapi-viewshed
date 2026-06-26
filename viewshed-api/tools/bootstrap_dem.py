import math
import os
import time
from array import array
from pathlib import Path
from urllib.parse import quote

import requests
from osgeo import gdal, osr


GEOSERVER_URL = os.environ.get("GEOSERVER_URL", "http://geoserver:8080/geoserver").rstrip("/")
GEOSERVER_USER = os.environ.get("GEOSERVER_USER", "admin")
GEOSERVER_PASSWORD = os.environ.get("GEOSERVER_PASSWORD", "geoserver-change-me")
DEM_PATH = Path(os.environ.get("DEM_PATH", "/data/dem/synthetic_dem.tif"))


def main() -> None:
    wait_for_geoserver()
    create_dem(DEM_PATH)
    publish_dem()
    print("OK: published dem:synthetic_dem")


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


def create_dem(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(path), 220, 220, 1, gdal.GDT_Float32, options=["TILED=YES", "COMPRESS=DEFLATE"])
    if dataset is None:
        raise RuntimeError(f"Could not create DEM: {path}")
    dataset.SetGeoTransform((700000.0, 30.0, 0.0, 3500000.0, 0.0, -30.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32636)
    dataset.SetProjection(srs.ExportToWkt())

    band = dataset.GetRasterBand(1)
    band.SetNoDataValue(-9999)
    width = dataset.RasterXSize
    height = dataset.RasterYSize
    values = array("f")
    for y in range(height):
        for x in range(width):
            ridge = 160.0 * math.exp(-(((x - 118) ** 2) / 900.0 + ((y - 95) ** 2) / 280.0))
            slope = 0.65 * x + 0.35 * y
            undulation = 18.0 * math.sin(x / 16.0) * math.cos(y / 22.0)
            values.append(120.0 + ridge + slope + undulation)
    band.WriteRaster(0, 0, width, height, values.tobytes(), buf_type=gdal.GDT_Float32)
    band.FlushCache()
    dataset.FlushCache()
    dataset = None


def publish_dem() -> None:
    session = requests.Session()
    session.auth = (GEOSERVER_USER, GEOSERVER_PASSWORD)
    ensure_workspace(session, "dem")
    store_path = "/rest/workspaces/dem/coveragestores/synthetic_dem"
    if exists(session, store_path + ".json"):
        return
    geoserver(
        session,
        "POST",
        "/rest/workspaces/dem/coveragestores",
        json={
            "coverageStore": {
                "name": "synthetic_dem",
                "type": "GeoTIFF",
                "enabled": True,
                "url": "file:///data/dem/synthetic_dem.tif",
                "workspace": {"name": "dem"},
            }
        },
    )
    geoserver(
        session,
        "POST",
        store_path + "/coverages",
        json={
            "coverage": {
                "name": "synthetic_dem",
                "nativeName": "synthetic_dem",
                "title": "Synthetic projected DEM",
                "enabled": True,
            }
        },
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
    response = session.request(method, f"{GEOSERVER_URL}{path}", timeout=90, **kwargs)
    if not response.ok:
        raise RuntimeError(f"GeoServer {method} {path} failed: {response.status_code} {response.text[:500]}")
    return response


if __name__ == "__main__":
    main()
