param(
    [string]$Version = "0.1.0",
    [string]$Repository = "ghcr.io/ashapira/pygeoapi-viewshed",
    [string]$ReportDir = ".\security-reports"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ReportDir)) {
    New-Item -ItemType Directory -Path $ReportDir | Out-Null
}

Push-Location .\viewer
try {
    npm audit --omit=dev
}
finally {
    Pop-Location
}

docker compose config --quiet
docker compose -f docker-compose.airgap.yml config --quiet

$runtimeSearch = @(rg -n "https?://|tile\.openstreetmap|cdn|unpkg|jsdelivr" `
    viewer\src viewer\index.html viewer\nginx.conf viewshed-api\pygeoapi-config.yml 2>$null)
$rgExit = $LASTEXITCODE
$externalRuntimeUrls = @(
    $runtimeSearch | Where-Object {
        $_ -notmatch "http://viewshed-api:" -and
        $_ -notmatch "http://geoserver:" -and
        $_ -notmatch "http://localhost" -and
        $_ -notmatch "http://127\.0\.0\.1"
    }
)
if ($externalRuntimeUrls.Count -gt 0) {
    $externalRuntimeUrls | Set-Content -Path (Join-Path $ReportDir "runtime-url-findings.txt")
    throw "Runtime URL scan found external URL references. See $ReportDir\runtime-url-findings.txt"
}
if ($rgExit -ne 0 -and $rgExit -ne 1) {
    throw "Runtime URL scan failed"
}
"OK: no external runtime URL references in viewer assets, nginx config, or pygeoapi map config." |
    Set-Content -Path (Join-Path $ReportDir "runtime-url-findings.txt")

$images = @(
    "$Repository-api:$Version",
    "$Repository-viewer:$Version",
    "$Repository-data:$Version",
    "docker.osgeo.org/geoserver:3.0.0"
)

foreach ($image in $images) {
    $safeName = ($image -replace '[/:]', '_')
    docker scout cves $image | Tee-Object -FilePath (Join-Path $ReportDir "$safeName-cves.txt")
    docker scout recommendations $image | Tee-Object -FilePath (Join-Path $ReportDir "$safeName-recommendations.txt")
}

Write-Host "OK: security checks complete. Reports are in $ReportDir"
