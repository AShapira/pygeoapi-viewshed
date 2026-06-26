param(
    [string]$Version = "0.1.0",
    [string]$Repository = "ghcr.io/ashapira/pygeoapi-viewshed"
)

$ErrorActionPreference = "Stop"

$apiImage = "$Repository-api"
$viewerImage = "$Repository-viewer"
$dataImage = "$Repository-data"
$dataFile = ".\data-image\srtm_center_israel_utm36.tif"

if (-not (Test-Path -LiteralPath $dataFile)) {
    throw "Missing $dataFile. Run .\scripts\Prepare-DataImage.ps1 first, or copy a prepared central-Israel SRTM COG there."
}

docker build `
    --build-arg APP_VERSION=$Version `
    -t "${apiImage}:$Version" `
    -t "${apiImage}:latest" `
    .\viewshed-api

docker build `
    --build-arg APP_VERSION=$Version `
    -t "${viewerImage}:$Version" `
    -t "${viewerImage}:latest" `
    .\viewer

docker build `
    --build-arg APP_VERSION=$Version `
    -t "${dataImage}:$Version" `
    -t "${dataImage}:latest" `
    .\data-image

Write-Host "OK: built $apiImage, $viewerImage, and $dataImage with tags $Version and latest"
