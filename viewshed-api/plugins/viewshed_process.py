import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import requests
from osgeo import gdal, ogr, osr
from pygeoapi.process.base import BaseProcessor, ProcessorExecuteError


PROCESS_METADATA = {
    "version": "0.1.0",
    "id": "viewshed",
    "title": "GDAL viewshed",
    "description": "Downloads a DEM coverage from GeoServer WCS, computes a GDAL viewshed, and publishes raster, vector, or both results to GeoServer.",
    "jobControlOptions": ["sync-execute"],
    "outputTransmission": ["value"],
    "keywords": ["viewshed", "gdal", "geoserver", "wcs", "raster", "vector"],
    "links": [],
    "inputs": {
        "demLayer": {
            "title": "DEM layer",
            "description": "GeoServer DEM layer name as workspace:layer. Default is dem:srtm_center_israel.",
            "schema": {"type": "string"},
            "minOccurs": 1,
            "maxOccurs": 1,
        },
        "observerX": {
            "title": "Observer X",
            "description": "Observer X coordinate in the DEM projected CRS.",
            "schema": {"type": "number"},
            "minOccurs": 1,
            "maxOccurs": 1,
        },
        "observerY": {
            "title": "Observer Y",
            "description": "Observer Y coordinate in the DEM projected CRS.",
            "schema": {"type": "number"},
            "minOccurs": 1,
            "maxOccurs": 1,
        },
        "observerHeight": {
            "title": "Observer height",
            "description": "Observer height above terrain, in DEM CRS vertical units.",
            "schema": {"type": "number", "minimum": 0},
            "minOccurs": 0,
            "maxOccurs": 1,
        },
        "targetHeight": {
            "title": "Target height",
            "description": "Target height above terrain, in DEM CRS vertical units.",
            "schema": {"type": "number", "minimum": 0},
            "minOccurs": 0,
            "maxOccurs": 1,
        },
        "maxDistance": {
            "title": "Maximum distance",
            "description": "Maximum viewshed distance in DEM CRS units. The service caps this value.",
            "schema": {"type": "number", "exclusiveMinimum": 0},
            "minOccurs": 1,
            "maxOccurs": 1,
        },
        "refractionCoefficient": {
            "title": "Refraction coefficient",
            "description": "Optional atmospheric refraction coefficient. Converted to GDAL curvature coefficient as 1 - refractionCoefficient.",
            "schema": {"type": "number", "minimum": 0, "maximum": 1},
            "minOccurs": 0,
            "maxOccurs": 1,
        },
        "outputName": {
            "title": "Output name",
            "description": "Optional GeoServer-safe layer/store suffix. A unique name is generated when omitted.",
            "schema": {"type": "string"},
            "minOccurs": 0,
            "maxOccurs": 1,
        },
        "outputType": {
            "title": "Output type",
            "description": "Result type to publish: raster, vector, or both. Defaults to raster.",
            "schema": {"type": "string", "enum": ["raster", "vector", "both"]},
            "minOccurs": 0,
            "maxOccurs": 1,
        },
    },
    "outputs": {
        "result": {
            "title": "Viewshed publication",
            "description": "GeoServer publication metadata and links for the generated viewshed raster.",
            "schema": {"type": "object", "contentMediaType": "application/json"},
        }
    },
    "example": {
        "inputs": {
            "demLayer": "dem:srtm_center_israel",
            "observerX": 703000,
            "observerY": 3497000,
            "observerHeight": 2,
            "targetHeight": 0,
            "maxDistance": 3000,
            "refractionCoefficient": 0.142857,
            "outputName": "viewshed_demo",
            "outputType": "raster",
        }
    },
}


class ViewshedProcessor(BaseProcessor):
    """pygeoapi process that wraps GDAL viewshed and GeoServer publication."""

    def __init__(self, processor_def: dict[str, Any]):
        super().__init__(processor_def, PROCESS_METADATA)
        self.geoserver_url = _required_env("GEOSERVER_URL").rstrip("/")
        self.geoserver_public_url = os.getenv("GEOSERVER_PUBLIC_URL", self.geoserver_url).rstrip("/")
        self.geoserver_user = _required_env("GEOSERVER_USER")
        self.geoserver_password = _required_env("GEOSERVER_PASSWORD")
        self.result_workspace = os.getenv("RESULT_WORKSPACE", "analysis")
        self.result_data_dir = Path(os.getenv("RESULT_DATA_DIR", "/data/results"))
        self.work_dir = Path(os.getenv("WORK_DIR", "/data/work"))
        self.allowed_dem_layers = {
            value.strip()
            for value in os.getenv("ALLOWED_DEM_LAYERS", "dem:srtm_center_israel,dem:synthetic_dem").split(",")
            if value.strip()
        }
        self.max_distance = float(os.getenv("MAX_DISTANCE_METERS", "5000"))
        self.http = requests.Session()
        self.http.auth = (self.geoserver_user, self.geoserver_password)

    def execute(self, data: dict[str, Any], outputs: dict[str, Any] | None = None):
        inputs = data.get("inputs", data)
        dem_layer = str(_input(inputs, "demLayer", required=True))
        if dem_layer not in self.allowed_dem_layers:
            raise ProcessorExecuteError(f"DEM layer is not allowed for this service: {dem_layer}")

        observer_x = _float_input(inputs, "observerX", required=True)
        observer_y = _float_input(inputs, "observerY", required=True)
        observer_height = _float_input(inputs, "observerHeight", default=2.0)
        target_height = _float_input(inputs, "targetHeight", default=0.0)
        max_distance = _float_input(inputs, "maxDistance", required=True)
        refraction_coefficient = _optional_float_input(inputs, "refractionCoefficient")
        if max_distance <= 0 or max_distance > self.max_distance:
            raise ProcessorExecuteError(
                f"maxDistance must be greater than 0 and no more than {self.max_distance:g}"
            )
        if observer_height < 0 or target_height < 0:
            raise ProcessorExecuteError("observerHeight and targetHeight must be non-negative")
        if refraction_coefficient is not None and not 0 <= refraction_coefficient <= 1:
            raise ProcessorExecuteError("refractionCoefficient must be between 0 and 1")
        curvature_coefficient = (
            1.0 - refraction_coefficient
            if refraction_coefficient is not None
            else None
        )

        output_name = _slug(str(_input(inputs, "outputName", default="")).strip())
        if not output_name:
            output_name = f"viewshed_{int(time.time())}"
        output_type = _output_type(str(_input(inputs, "outputType", default="raster")))

        workspace, layer = _split_layer(dem_layer)
        run_dir = self.work_dir / output_name
        run_dir.mkdir(parents=True, exist_ok=True)
        self.result_data_dir.mkdir(parents=True, exist_ok=True)
        source_tif = run_dir / "source-dem.tif"
        raw_viewshed = run_dir / "viewshed-raw.tif"
        result_tif = self.result_data_dir / f"{output_name}.tif"
        vector_name = f"{output_name}_vector"
        vector_dir = self.result_data_dir / vector_name
        vector_shp = vector_dir / f"{vector_name}.shp"

        try:
            self._download_wcs(workspace, layer, source_tif)
            bbox, native_crs = _dataset_bbox_and_crs(source_tif)
            _assert_projected(source_tif)
            viewshed_command = [
                "gdal_viewshed",
                "-b",
                "1",
                "-ox",
                str(observer_x),
                "-oy",
                str(observer_y),
                "-oz",
                str(observer_height),
                "-tz",
                str(target_height),
                "-md",
                str(max_distance),
                "-vv",
                "1",
                "-iv",
                "0",
                "-ov",
                "0",
                "-f",
                "GTiff",
                str(source_tif),
                str(raw_viewshed),
            ]
            if curvature_coefficient is not None:
                viewshed_command[viewshed_command.index("-f"):viewshed_command.index("-f")] = [
                    "-cc",
                    f"{curvature_coefficient:.12g}",
                ]
            _run(*viewshed_command)
            if output_type in {"raster", "both"}:
                _run(
                    "gdal_translate",
                    str(raw_viewshed),
                    str(result_tif),
                    "-of",
                    "COG",
                    "-co",
                    "COMPRESS=DEFLATE",
                    "-co",
                    "BIGTIFF=IF_SAFER",
                )
                self._publish_raster_result(output_name, result_tif)
            if output_type in {"vector", "both"}:
                _polygonize_viewshed(raw_viewshed, vector_shp, vector_name)
                self._publish_vector_result(vector_name, vector_shp)
        except ProcessorExecuteError:
            raise
        except Exception as exc:
            raise ProcessorExecuteError(str(exc)) from exc

        result = {
            "status": "published",
            "workspace": self.result_workspace,
            "outputType": output_type,
            "bbox": bbox,
            "refractionCoefficient": refraction_coefficient,
            "curvatureCoefficient": curvature_coefficient,
        }
        if output_type in {"raster", "both"}:
            result["raster"] = self._raster_result(output_name, bbox, native_crs)
        if output_type in {"vector", "both"}:
            result["vector"] = self._vector_result(vector_name, bbox, native_crs)
        return "application/json", {"result": result}

    def _raster_result(self, output_name: str, bbox: list[float], native_crs: str) -> dict[str, Any]:
        layer_name = f"{self.result_workspace}:{output_name}"
        wms_query = urlencode(
            {
                "service": "WMS",
                "version": "1.1.1",
                "request": "GetMap",
                "layers": layer_name,
                "styles": "",
                "srs": native_crs,
                "bbox": ",".join(str(value) for value in bbox),
                "width": 800,
                "height": 600,
                "format": "image/png",
            }
        )
        wcs_query = urlencode(
            {
                "service": "WCS",
                "version": "2.0.1",
                "request": "GetCoverage",
                "coverageId": f"{self.result_workspace}__{output_name}",
                "format": "image/tiff",
            }
        )
        return {
            "coverageStore": output_name,
            "layerName": layer_name,
            "geotiffPath": f"file:///data/results/{output_name}.tif",
            "wmsUrl": f"{self.geoserver_public_url}/wms?{wms_query}",
            "wcsUrl": f"{self.geoserver_public_url}/{self.result_workspace}/ows?{wcs_query}",
        }

    def _vector_result(self, vector_name: str, bbox: list[float], native_crs: str) -> dict[str, Any]:
        layer_name = f"{self.result_workspace}:{vector_name}"
        wms_query = urlencode(
            {
                "service": "WMS",
                "version": "1.1.1",
                "request": "GetMap",
                "layers": layer_name,
                "styles": "",
                "srs": native_crs,
                "bbox": ",".join(str(value) for value in bbox),
                "width": 800,
                "height": 600,
                "format": "image/png",
            }
        )
        wfs_query = urlencode(
            {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": layer_name,
                "outputFormat": "application/json",
            }
        )
        return {
            "dataStore": vector_name,
            "layerName": layer_name,
            "shapefilePath": f"file:///data/results/{vector_name}/{vector_name}.shp",
            "wmsUrl": f"{self.geoserver_public_url}/wms?{wms_query}",
            "wfsUrl": f"{self.geoserver_public_url}/{self.result_workspace}/ows?{wfs_query}",
        }

    def _download_wcs(self, workspace: str, layer: str, target: Path) -> None:
        coverage_id = f"{workspace}__{layer}"
        query = {
            "service": "WCS",
            "version": "2.0.1",
            "request": "GetCoverage",
            "coverageId": coverage_id,
            "format": "image/tiff",
        }
        response = self.http.get(f"{self.geoserver_url}/{workspace}/ows", params=query, timeout=120)
        if not response.ok:
            raise ProcessorExecuteError(
                f"GeoServer WCS GetCoverage failed: {response.status_code} {response.text[:500]}"
            )
        content_type = response.headers.get("Content-Type", "")
        if "xml" in content_type.lower():
            raise ProcessorExecuteError(f"GeoServer WCS returned XML instead of GeoTIFF: {response.text[:500]}")
        target.write_bytes(response.content)

    def _publish_raster_result(self, output_name: str, result_tif: Path) -> None:
        self._ensure_workspace(self.result_workspace)
        store = quote(output_name, safe="")
        workspace = quote(self.result_workspace, safe="")
        store_path = f"/rest/workspaces/{workspace}/coveragestores/{store}"
        if self._exists(store_path + ".json"):
            raise ProcessorExecuteError(f"Result coverage store already exists: {self.result_workspace}:{output_name}")
        self._geoserver(
            "PUT",
            f"{store_path}/external.geotiff?configure=first&coverageName={quote(output_name, safe='')}",
            data=result_tif.as_posix(),
            headers={"Content-Type": "text/plain"},
        )

    def _publish_vector_result(self, vector_name: str, vector_shp: Path) -> None:
        self._ensure_workspace(self.result_workspace)
        store = quote(vector_name, safe="")
        workspace = quote(self.result_workspace, safe="")
        store_path = f"/rest/workspaces/{workspace}/datastores/{store}"
        if self._exists(store_path + ".json"):
            raise ProcessorExecuteError(f"Result data store already exists: {self.result_workspace}:{vector_name}")
        self._geoserver(
            "PUT",
            f"{store_path}/external.shp?configure=all",
            data=vector_shp.as_posix(),
            headers={"Content-Type": "text/plain"},
        )

    def _ensure_workspace(self, workspace: str) -> None:
        encoded = quote(workspace, safe="")
        if not self._exists(f"/rest/workspaces/{encoded}.json"):
            self._geoserver("POST", "/rest/workspaces", json={"workspace": {"name": workspace}})

    def _exists(self, path: str) -> bool:
        response = self.http.get(f"{self.geoserver_url}{path}", timeout=30)
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def _geoserver(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        response = self.http.request(method, f"{self.geoserver_url}{path}", timeout=90, **kwargs)
        if not response.ok:
            raise ProcessorExecuteError(f"GeoServer {method} {path} failed: {response.status_code} {response.text[:500]}")
        return response

    def __repr__(self):
        return "<ViewshedProcessor>"


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable is not set: {name}")
    return value


def _input(inputs: dict[str, Any], name: str, default: Any = None, required: bool = False) -> Any:
    value = inputs.get(name, default)
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if required and value is None:
        raise ProcessorExecuteError(f"Missing required input: {name}")
    return value


def _float_input(inputs: dict[str, Any], name: str, default: float | None = None, required: bool = False) -> float:
    value = _input(inputs, name, default=default, required=required)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProcessorExecuteError(f"Input must be numeric: {name}") from exc


def _optional_float_input(inputs: dict[str, Any], name: str) -> float | None:
    value = _input(inputs, name, default=None)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProcessorExecuteError(f"Input must be numeric: {name}") from exc


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return value[:48]


def _output_type(value: str) -> str:
    value = value.strip().lower() or "raster"
    if value not in {"raster", "vector", "both"}:
        raise ProcessorExecuteError("outputType must be one of: raster, vector, both")
    return value


def _split_layer(layer_name: str) -> tuple[str, str]:
    if ":" not in layer_name:
        raise ProcessorExecuteError("Layer name must use workspace:layer syntax")
    workspace, layer = layer_name.split(":", 1)
    if not workspace or not layer:
        raise ProcessorExecuteError("Layer name must use workspace:layer syntax")
    return workspace, layer


def _run(*arguments: str) -> None:
    completed = subprocess.run(arguments, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        raise ProcessorExecuteError(
            f"Command failed: {' '.join(arguments)}\nSTDOUT: {completed.stdout[-1000:]}\nSTDERR: {completed.stderr[-1000:]}"
        )


def _assert_projected(path: Path) -> None:
    dataset = gdal.Open(str(path))
    if dataset is None:
        raise ProcessorExecuteError(f"GDAL could not open downloaded DEM: {path}")
    srs = osr.SpatialReference()
    if srs.ImportFromWkt(dataset.GetProjection()) != 0:
        raise ProcessorExecuteError("Downloaded DEM has no readable CRS")
    if srs.IsGeographic():
        raise ProcessorExecuteError("Downloaded DEM CRS is geographic; viewshed service requires projected coordinates")


def _polygonize_viewshed(source_raster: Path, target_shp: Path, layer_name: str) -> None:
    target_shp.parent.mkdir(parents=True, exist_ok=True)
    driver = ogr.GetDriverByName("ESRI Shapefile")
    if driver is None:
        raise ProcessorExecuteError("GDAL ESRI Shapefile driver is not available")
    if target_shp.parent.exists():
        for path in target_shp.parent.glob(f"{target_shp.stem}.*"):
            path.unlink()

    raster = gdal.Open(str(source_raster))
    if raster is None:
        raise ProcessorExecuteError(f"GDAL could not open viewshed raster: {source_raster}")
    band = raster.GetRasterBand(1)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(raster.GetProjection())

    datasource = driver.CreateDataSource(str(target_shp))
    if datasource is None:
        raise ProcessorExecuteError(f"GDAL could not create shapefile: {target_shp}")
    layer = datasource.CreateLayer(layer_name, srs=srs, geom_type=ogr.wkbPolygon)
    if layer is None:
        raise ProcessorExecuteError(f"GDAL could not create shapefile layer: {layer_name}")
    field = ogr.FieldDefn("Visible", ogr.OFTInteger)
    if layer.CreateField(field) != 0:
        raise ProcessorExecuteError("GDAL could not create Visible field")
    field_index = layer.GetLayerDefn().GetFieldIndex("Visible")
    if gdal.Polygonize(band, None, layer, field_index, [], callback=None) != 0:
        raise ProcessorExecuteError("GDAL polygonize failed")
    layer.SyncToDisk()
    datasource = None
    raster = None


def _dataset_bbox_and_crs(path: Path) -> tuple[list[float], str]:
    dataset = gdal.Open(str(path))
    if dataset is None:
        raise ProcessorExecuteError(f"GDAL could not open raster: {path}")
    gt = dataset.GetGeoTransform()
    width = dataset.RasterXSize
    height = dataset.RasterYSize
    minx = gt[0]
    maxy = gt[3]
    maxx = gt[0] + width * gt[1] + height * gt[2]
    miny = gt[3] + width * gt[4] + height * gt[5]
    srs = osr.SpatialReference()
    srs.ImportFromWkt(dataset.GetProjection())
    authority_name = srs.GetAuthorityName(None) or "EPSG"
    authority_code = srs.GetAuthorityCode(None) or "32636"
    return [min(minx, maxx), min(miny, maxy), max(minx, maxx), max(miny, maxy)], f"{authority_name}:{authority_code}"
