# One-shot Cloud Run deploy + post-deploy cleanup.
# Builds from the current source, deploys with the pinned env-vars/secrets,
# health-checks the new URL, then prunes old revisions/images/secret versions.
#
# Usage:
#   .\deploy\cloud-run\deploy.ps1
#   .\deploy\cloud-run\deploy.ps1 -SkipCleanup

param(
    [string]$Service = "vibescape",
    [string]$Region  = "us-central1",
    [switch]$SkipCleanup
)

$gcloud = "C:\Users\chand\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$repo   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo

$Project = (& $gcloud config get-value project 2>$null).Trim()
$serviceUrl = "https://$Service-241988497106.$Region.run.app"

Write-Host "Project: $Project"
Write-Host "Service: $Service ($Region)"
Write-Host ""

$envVars = "VIBESCAPE_ML_MODE=modal,DB_BACKEND=turso,SPOTIFY_REDIRECT_URI=$serviceUrl/callback"
$secrets = "SPOTIFY_CLIENT_ID=SPOTIFY_CLIENT_ID:latest," +
           "SPOTIFY_CLIENT_SECRET=SPOTIFY_CLIENT_SECRET:latest," +
           "MODAL_TOKEN_ID=MODAL_TOKEN_ID:latest," +
           "MODAL_TOKEN_SECRET=MODAL_TOKEN_SECRET:latest," +
           "TURSO_DATABASE_URL=TURSO_DATABASE_URL:latest," +
           "TURSO_AUTH_TOKEN=TURSO_AUTH_TOKEN:latest"

Write-Host "=== deploy ==="
& $gcloud run deploy $Service --source . --region $Region --allow-unauthenticated --port 8080 --memory 512Mi --cpu 1 --min-instances 0 --max-instances 3 --timeout 300 --set-env-vars $envVars --set-secrets $secrets --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Error "Deploy failed (exit $LASTEXITCODE). Skipping cleanup."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "=== health check ==="
$healthUrl = "$serviceUrl/api/health"
$ok = $false
for ($i = 1; $i -le 8; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 15
        if ($resp.StatusCode -eq 200) {
            Write-Host "  attempt $i : HTTP 200 -- $($resp.Content)"
            $ok = $true
            break
        } else {
            Write-Host "  attempt $i : HTTP $($resp.StatusCode)"
        }
    } catch {
        Write-Host "  attempt $i : $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 3
}
if (-not $ok) {
    Write-Error "Health check failed after 8 attempts. Not running cleanup."
    exit 1
}

if ($SkipCleanup) {
    Write-Host ""
    Write-Host "Skipping cleanup (--SkipCleanup)."
    exit 0
}

Write-Host ""
Write-Host "=== cleanup ==="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "cleanup.ps1")

Write-Host ""
Write-Host "Deploy + cleanup complete. URL: $serviceUrl"
