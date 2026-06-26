param(
    [string]$Version = "0.1.0",
    [string]$Repository = "ghcr.io/ashapira/pygeoapi-viewshed"
)

$ErrorActionPreference = "Stop"

$images = @(
    "$Repository-api",
    "$Repository-viewer",
    "$Repository-data"
)

foreach ($image in $images) {
    docker push "${image}:$Version"
    docker push "${image}:latest"
}

Write-Host "OK: pushed GHCR images for $Version and latest"
