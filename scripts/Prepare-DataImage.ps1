param(
    [string]$ComposeFile = "docker-compose.yml",
    [string]$ContainerName = "pygeoapi-viewshed-api",
    [string]$SourcePath = "/data/dem/srtm_center_israel_utm36.tif",
    [string]$OutputPath = ".\data-image\srtm_center_israel_utm36.tif"
)

$ErrorActionPreference = "Stop"

docker compose -f $ComposeFile up -d geoserver viewshed-api
docker compose -f $ComposeFile --profile tools run --rm bootstrap-srtm

$outputDir = Split-Path -Parent $OutputPath
if ($outputDir -and -not (Test-Path -LiteralPath $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir | Out-Null
}

docker cp "${ContainerName}:${SourcePath}" $OutputPath

$item = Get-Item -LiteralPath $OutputPath
if ($item.Length -le 0) {
    throw "Prepared DEM file is empty: $OutputPath"
}

Write-Host "OK: prepared $OutputPath ($($item.Length) bytes)"
