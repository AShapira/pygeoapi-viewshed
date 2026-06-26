param(
    [string]$PygeoapiUrl = "http://localhost:5000",
    [string]$GeoServerUrl = "http://localhost:8088/geoserver",
    [string]$ViewerUrl = "http://localhost:8090",
    [string]$GeoServerUser = "admin",
    [string]$GeoServerPassword = "geoserver-change-me",
    [int]$TimeoutSeconds = 360
)

$ErrorActionPreference = "Stop"

function Invoke-Native {
    & $args[0] @($args | Select-Object -Skip 1)
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($args -join ' ')"
    }
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

$authBytes = [Text.Encoding]::ASCII.GetBytes("${GeoServerUser}:${GeoServerPassword}")
$authHeader = @{ Authorization = "Basic " + [Convert]::ToBase64String($authBytes) }
$runId = Get-Date -Format "yyyyMMddHHmmss"

function Invoke-Viewshed {
    param(
        [string]$OutputName,
        [string]$OutputType,
        [object]$RefractionCoefficient = $null,
        [string]$BaseUrl = $PygeoapiUrl
    )
    $inputs = @{
        demLayer = "dem:synthetic_dem"
        observerX = 703000
        observerY = 3497000
        observerHeight = 2
        targetHeight = 0
        maxDistance = 3000
        outputName = $OutputName
        outputType = $OutputType
    }
    if ($null -ne $RefractionCoefficient) {
        $inputs.refractionCoefficient = $RefractionCoefficient
    }
    $body = @{
        inputs = $inputs
    } | ConvertTo-Json -Depth 8

    Invoke-RestMethod -Uri "$BaseUrl/processes/viewshed/execution" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 180
}

function Assert-ViewshedRejected {
    param([object]$RefractionCoefficient, [string]$Label)
    $body = @{
        inputs = @{
            demLayer = "dem:synthetic_dem"
            observerX = 703000
            observerY = 3497000
            maxDistance = 3000
            outputName = "viewshed_bad_refraction_${Label}_$runId"
            outputType = "raster"
            refractionCoefficient = $RefractionCoefficient
        }
    } | ConvertTo-Json -Depth 8
    $rejected = $false
    try {
        Invoke-RestMethod -Uri "$PygeoapiUrl/processes/viewshed/execution" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 60 | Out-Null
    }
    catch {
        $rejected = $true
    }
    Assert-True $rejected "Invalid refractionCoefficient was not rejected: $RefractionCoefficient"
}

function Assert-Layer {
    param([string]$LayerName)
    $layerEncoded = [uri]::EscapeDataString($LayerName)
    $layer = Invoke-RestMethod -Uri "$GeoServerUrl/rest/layers/$layerEncoded.json" -Headers $authHeader -TimeoutSec 30
    Assert-True ($layer.layer.resource.name -eq $LayerName) "GeoServer layer was not found: $LayerName"
}

function Assert-WfsVisible {
    param([string]$WfsUrl)
    $featureCollection = Invoke-RestMethod -Uri $WfsUrl -TimeoutSec 60
    Assert-True (@($featureCollection.features).Count -gt 0) "Vector WFS returned no features."
    $values = @()
    foreach ($feature in $featureCollection.features) {
        $property = $feature.properties.PSObject.Properties | Where-Object { $_.Name -ieq "Visible" } | Select-Object -First 1
        Assert-True ($null -ne $property) "Vector WFS feature did not contain Visible property."
        $values += [int]$property.Value
    }
    $invalid = @($values | Where-Object { $_ -ne 0 -and $_ -ne 1 })
    Assert-True ($invalid.Count -eq 0) "Visible field contained values other than 0 or 1: $($invalid -join ', ')"
}

Write-Host "Validating Compose configuration ..."
Invoke-Native docker compose config --quiet

Write-Host "Starting stack ..."
Invoke-Native docker compose up -d --build

Write-Host "Waiting for GeoServer, pygeoapi, and viewer ..."
Wait-HttpOk "$GeoServerUrl/web/"
Wait-HttpOk "$PygeoapiUrl/"
Wait-HttpOk "$ViewerUrl/"

Write-Host "Checking viewer app shell ..."
$viewer = Invoke-WebRequest -UseBasicParsing -Uri "$ViewerUrl/" -TimeoutSec 30
Assert-True ($viewer.Content -match "Viewshed Control") "Viewer app HTML did not load."

Write-Host "Publishing synthetic DEM ..."
Invoke-Native docker compose --profile tools run --rm bootstrap-dem

Write-Host "Checking OGC API - Processes discovery ..."
$landing = Invoke-RestMethod -Uri "$PygeoapiUrl/" -TimeoutSec 30
Assert-True ($null -ne $landing.links) "pygeoapi landing page did not return links."
$process = Invoke-RestMethod -Uri "$PygeoapiUrl/processes/viewshed" -TimeoutSec 30
Assert-True ($process.id -eq "viewshed") "Unexpected process id: $($process.id)"

Write-Host "Checking DEM WCS availability ..."
$wcs = Invoke-WebRequest -UseBasicParsing -Uri "$GeoServerUrl/dem/ows?service=WCS&version=2.0.1&request=GetCapabilities" -Headers $authHeader -TimeoutSec 30
Assert-True ($wcs.StatusCode -eq 200) "WCS capabilities did not return HTTP 200."

Write-Host "Executing viewshed through viewer gateway ..."
$gatewayName = "viewshed_gateway_$runId"
$execution = Invoke-Viewshed -OutputName $gatewayName -OutputType "both" -RefractionCoefficient 0.142857 -BaseUrl $ViewerUrl
$result = $execution.result
Assert-True ($result.status -eq "published") "Unexpected gateway result status: $($result.status)"
Assert-True ($result.outputType -eq "both") "Unexpected gateway outputType: $($result.outputType)"
Assert-True ($result.raster.wmsUrl.StartsWith("$ViewerUrl/geoserver")) "Gateway raster WMS URL did not use viewer GeoServer proxy: $($result.raster.wmsUrl)"
Assert-True ($result.raster.wcsUrl.StartsWith("$ViewerUrl/geoserver")) "Gateway raster WCS URL did not use viewer GeoServer proxy: $($result.raster.wcsUrl)"
Assert-True ($result.vector.wmsUrl.StartsWith("$ViewerUrl/geoserver")) "Gateway vector WMS URL did not use viewer GeoServer proxy: $($result.vector.wmsUrl)"
Assert-True ($result.vector.wfsUrl.StartsWith("$ViewerUrl/geoserver")) "Gateway vector WFS URL did not use viewer GeoServer proxy: $($result.vector.wfsUrl)"
Assert-Layer "analysis:$gatewayName"
Assert-Layer "analysis:${gatewayName}_vector"
$wms = Invoke-WebRequest -UseBasicParsing -Uri $result.raster.wmsUrl -TimeoutSec 60
Assert-True ($wms.StatusCode -eq 200) "Gateway raster WMS did not return HTTP 200."
Assert-WfsVisible $result.vector.wfsUrl

Write-Host "Executing raster-only viewshed process ..."
$rasterName = "viewshed_raster_$runId"
$execution = Invoke-Viewshed -OutputName $rasterName -OutputType "raster"
$result = $execution.result
Assert-True ($result.status -eq "published") "Unexpected viewshed result status: $($result.status)"
Assert-True ($result.outputType -eq "raster") "Unexpected outputType: $($result.outputType)"
Assert-True ($null -ne $result.raster) "Raster result was missing."
Assert-True ($null -eq $result.vector) "Vector result was unexpectedly present."
Assert-True ($result.raster.layerName -eq "analysis:$rasterName") "Unexpected raster layerName: $($result.raster.layerName)"

Write-Host "Checking raster result layer through GeoServer REST ..."
Assert-Layer "analysis:$rasterName"

Write-Host "Checking raster result WMS ..."
$wms = Invoke-WebRequest -UseBasicParsing -Uri $result.raster.wmsUrl -TimeoutSec 60
Assert-True ($wms.StatusCode -eq 200) "Result WMS did not return HTTP 200."

Write-Host "Checking generated COG with gdalinfo ..."
Invoke-Native docker compose exec -T viewshed-api gdalinfo "/data/results/$rasterName.tif"

Write-Host "Executing vector-only viewshed process ..."
$vectorName = "viewshed_vector_$runId"
$execution = Invoke-Viewshed -OutputName $vectorName -OutputType "vector"
$result = $execution.result
Assert-True ($result.status -eq "published") "Unexpected vector result status: $($result.status)"
Assert-True ($result.outputType -eq "vector") "Unexpected outputType: $($result.outputType)"
Assert-True ($null -eq $result.raster) "Raster result was unexpectedly present."
Assert-True ($null -ne $result.vector) "Vector result was missing."
Assert-True ($result.vector.layerName -eq "analysis:${vectorName}_vector") "Unexpected vector layerName: $($result.vector.layerName)"

Write-Host "Checking vector result layer through GeoServer REST ..."
Assert-Layer "analysis:${vectorName}_vector"
Invoke-Native docker compose exec -T viewshed-api test -f "/data/results/${vectorName}_vector/${vectorName}_vector.shp"

Write-Host "Checking vector result WMS and WFS ..."
$wms = Invoke-WebRequest -UseBasicParsing -Uri $result.vector.wmsUrl -TimeoutSec 60
Assert-True ($wms.StatusCode -eq 200) "Vector WMS did not return HTTP 200."
Assert-WfsVisible $result.vector.wfsUrl

Write-Host "Executing combined raster/vector viewshed process ..."
$bothName = "viewshed_both_$runId"
$execution = Invoke-Viewshed -OutputName $bothName -OutputType "both" -RefractionCoefficient 0.142857
$result = $execution.result
Assert-True ($result.status -eq "published") "Unexpected combined result status: $($result.status)"
Assert-True ($result.outputType -eq "both") "Unexpected outputType: $($result.outputType)"
Assert-True ([math]::Abs([double]$result.refractionCoefficient - 0.142857) -lt 0.000001) "Unexpected refractionCoefficient: $($result.refractionCoefficient)"
Assert-True ([math]::Abs([double]$result.curvatureCoefficient - 0.857143) -lt 0.000001) "Unexpected curvatureCoefficient: $($result.curvatureCoefficient)"
Assert-True ($null -ne $result.raster) "Combined raster result was missing."
Assert-True ($null -ne $result.vector) "Combined vector result was missing."
Assert-Layer "analysis:$bothName"
Assert-Layer "analysis:${bothName}_vector"
Invoke-Native docker compose exec -T viewshed-api gdalinfo "/data/results/$bothName.tif"
Invoke-Native docker compose exec -T viewshed-api test -f "/data/results/${bothName}_vector/${bothName}_vector.shp"

Write-Host "Checking invalid outputType rejection ..."
$badBody = @{
    inputs = @{
        demLayer = "dem:synthetic_dem"
        observerX = 703000
        observerY = 3497000
        maxDistance = 3000
        outputName = "viewshed_bad_$runId"
        outputType = "mesh"
    }
} | ConvertTo-Json -Depth 8
$rejected = $false
try {
    Invoke-RestMethod -Uri "$PygeoapiUrl/processes/viewshed/execution" -Method Post -ContentType "application/json" -Body $badBody -TimeoutSec 60 | Out-Null
}
catch {
    $rejected = $true
}
Assert-True $rejected "Invalid outputType was not rejected."

Write-Host "Checking invalid refractionCoefficient rejection ..."
Assert-ViewshedRejected -RefractionCoefficient -0.1 -Label "negative"
Assert-ViewshedRejected -RefractionCoefficient 1.1 -Label "large"
Assert-ViewshedRejected -RefractionCoefficient "abc" -Label "text"

Write-Host "OK: pygeoapi viewshed validated for raster, vector, and combined outputs."
