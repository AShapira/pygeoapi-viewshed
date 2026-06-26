param(
    [string]$PygeoapiUrl = "http://localhost:5000",
    [string]$GeoServerUrl = "http://localhost:8088/geoserver",
    [string]$ViewerUrl = "http://localhost:8090",
    [string]$GeoServerUser = "admin",
    [string]$GeoServerPassword = "geoserver-change-me",
    [int]$TimeoutSeconds = 480
)

$ErrorActionPreference = "Stop"

function Invoke-Native {
    & $args[0] @($args | Select-Object -Skip 1)
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($args -join ' ')"
    }
}

function Invoke-NativeCapture {
    $output = & $args[0] @($args | Select-Object -Skip 1) 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($args -join ' ')`n$($output -join "`n")"
    }
    $output -join "`n"
}

function Wait-HttpOk {
    param([string]$Uri, [hashtable]$Headers = @{})
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -Headers $Headers -TimeoutSec 10
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return
            }
        }
        catch {
            Start-Sleep -Seconds 3
        }
    }
    throw "Timed out waiting for $Uri"
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function Assert-Layer {
    param([string]$LayerName)
    $layerEncoded = [uri]::EscapeDataString($LayerName)
    $layer = Invoke-RestMethod -Uri "$GeoServerUrl/rest/layers/$layerEncoded.json" -Headers $authHeader -TimeoutSec 30
    Assert-True ($null -ne $layer.layer) "GeoServer layer was not found: $LayerName"
}

$authBytes = [Text.Encoding]::ASCII.GetBytes("${GeoServerUser}:${GeoServerPassword}")
$authHeader = @{ Authorization = "Basic " + [Convert]::ToBase64String($authBytes) }
$runId = Get-Date -Format "yyyyMMddHHmmss"

Write-Host "Validating Compose configuration ..."
Invoke-Native docker compose config --quiet

Write-Host "Starting stack ..."
Invoke-Native docker compose up -d --build geoserver viewshed-api viewer

Write-Host "Waiting for GeoServer, pygeoapi, and viewer ..."
Wait-HttpOk "$GeoServerUrl/web/"
Wait-HttpOk "$PygeoapiUrl/"
Wait-HttpOk "$ViewerUrl/"

Write-Host "Downloading and publishing central Israel SRTM ..."
Invoke-Native docker compose --profile tools run --rm bootstrap-srtm

Write-Host "Checking SRTM GeoServer layer ..."
Assert-Layer "dem:srtm_center_israel"

Write-Host "Checking SRTM WMS ..."
$wmsUrl = "$GeoServerUrl/wms?service=WMS&version=1.1.1&request=GetMap&layers=dem:srtm_center_israel&styles=&srs=EPSG:32636&bbox=645532,3480514,751831,3604500&width=512&height=512&format=image/png"
$wms = Invoke-WebRequest -UseBasicParsing -Uri $wmsUrl -TimeoutSec 60
Assert-True ($wms.StatusCode -eq 200) "SRTM WMS did not return HTTP 200."

Write-Host "Checking SRTM WCS ..."
$wcsUrl = "$GeoServerUrl/dem/ows?service=WCS&version=2.0.1&request=GetCoverage&coverageId=dem__srtm_center_israel&format=image/tiff"
$wcs = Invoke-WebRequest -UseBasicParsing -Uri $wcsUrl -Headers $authHeader -TimeoutSec 120
Assert-True ($wcs.StatusCode -eq 200) "SRTM WCS did not return HTTP 200."
Assert-True ($wcs.RawContentLength -gt 1024) "SRTM WCS response was unexpectedly small."

Write-Host "Checking generated SRTM raster metadata ..."
$info = Invoke-NativeCapture docker compose exec -T viewshed-api gdalinfo "/data/dem/srtm_center_israel_utm36.tif"
Assert-True ($info -match 'ID\["EPSG",32636\]') "SRTM raster did not report EPSG:32636."
Assert-True ($info -match 'Pixel Size = \(30\.') "SRTM raster did not report approximately 30 m pixels."

Write-Host "Executing raster-only viewshed against SRTM ..."
$outputName = "viewshed_srtm_$runId"
$body = @{
    inputs = @{
        demLayer = "dem:srtm_center_israel"
        observerX = 703000
        observerY = 3497000
        observerHeight = 2
        targetHeight = 0
        maxDistance = 3000
        refractionCoefficient = 0.142857
        outputName = $outputName
        outputType = "raster"
    }
} | ConvertTo-Json -Depth 8
$execution = Invoke-RestMethod -Uri "$PygeoapiUrl/processes/viewshed/execution" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 240
$result = $execution.result
Assert-True ($result.status -eq "published") "Unexpected SRTM viewshed result status: $($result.status)"
Assert-True ($result.raster.layerName -eq "analysis:$outputName") "Unexpected SRTM viewshed layer: $($result.raster.layerName)"
Assert-Layer "analysis:$outputName"

Write-Host "Checking SRTM viewshed WMS through viewer gateway ..."
$viewshedWms = Invoke-WebRequest -UseBasicParsing -Uri $result.raster.wmsUrl -TimeoutSec 60
Assert-True ($viewshedWms.StatusCode -eq 200) "SRTM viewshed WMS did not return HTTP 200."

Write-Host "Checking viewer app shell ..."
$viewer = Invoke-WebRequest -UseBasicParsing -Uri "$ViewerUrl/" -TimeoutSec 30
Assert-True ($viewer.Content -match "Viewshed Control") "Viewer app HTML did not load."

Write-Host "OK: SRTM central Israel DEM validated and usable for viewshed."
