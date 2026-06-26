param(
    [string]$PygeoapiUrl = "http://localhost:5000",
    [string]$GeoServerUrl = "http://localhost:8088/geoserver",
    [string]$ViewerUrl = "http://localhost:8090",
    [string]$GeoServerUser = "admin",
    [string]$GeoServerPassword = "geoserver-change-me"
)

$ErrorActionPreference = "Stop"

function Invoke-Json {
    param(
        [string]$Uri,
        [string]$Method = "GET",
        [object]$Body = $null
    )
    $headers = @{ Accept = "application/json" }
    if ($Body -ne $null) {
        $json = $Body | ConvertTo-Json -Depth 20
        return Invoke-RestMethod -Uri $Uri -Method $Method -Headers $headers -ContentType "application/json" -Body $json
    }
    Invoke-RestMethod -Uri $Uri -Method $Method -Headers $headers
}

function Wait-Http {
    param([string]$Uri, [int]$TimeoutSeconds = 240)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 10
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        }
        catch {
            Start-Sleep -Seconds 3
        }
    }
    throw "Timed out waiting for $Uri"
}

docker compose -f docker-compose.airgap.yml config --quiet
docker compose -f docker-compose.airgap.yml up -d --force-recreate geoserver viewshed-api viewer
docker compose -f docker-compose.airgap.yml --profile tools run --rm seed-srtm
docker compose -f docker-compose.airgap.yml --profile tools run --rm bootstrap-srtm

Wait-Http "$GeoServerUrl/web/"
Wait-Http "$PygeoapiUrl/processes"
Wait-Http "$ViewerUrl/"

$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${GeoServerUser}:${GeoServerPassword}"))
$headers = @{ Authorization = "Basic $auth" }
$layer = Invoke-RestMethod -Uri "$GeoServerUrl/rest/layers/dem:srtm_center_israel.json" -Headers $headers
if ($layer.layer.name -ne "srtm_center_israel" -or $layer.layer.resource.name -ne "dem:srtm_center_israel") {
    throw "SRTM layer was not published in GeoServer"
}

$runId = Get-Date -Format "yyyyMMddHHmmss"
$body = @{
    inputs = @{
        demLayer = "dem:srtm_center_israel"
        observerX = 703000
        observerY = 3497000
        observerHeight = 2
        targetHeight = 0
        maxDistance = 3000
        refractionCoefficient = 0.142857
        outputType = "raster"
        outputName = "airgap_validation_$runId"
    }
}
$result = Invoke-Json -Uri "$PygeoapiUrl/processes/viewshed/execution" -Method POST -Body $body
if (-not $result.result.raster.wmsUrl.Contains("localhost:8090/geoserver")) {
    throw "Result WMS URL does not use the viewer gateway"
}

Write-Host "OK: air-gap compose path seeded SRTM, published GeoServer layer, and executed viewshed."
