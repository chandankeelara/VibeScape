# One-shot loader: reads credentials from local sources and creates/updates
# GCP Secret Manager secrets. Values never printed. Safe to re-run.
param(
    [string]$ProjectId = (& "C:\Users\chand\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd" config get-value project 2>$null).Trim()
)

$gcloud = "C:\Users\chand\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$py = "D:\Softwares\MiniConda\python.exe"
$repo = "D:\Git\virtual457-projects\VibeScape"

Set-Location $repo

Write-Host "Loading secrets into project: $ProjectId"

$spotify_creds = & $py -c "import sys; sys.path.insert(0,'.'); import config as c; print(c.SPOTIFY_CLIENT_ID); print(c.SPOTIFY_CLIENT_SECRET)"
$spotify_id = $spotify_creds[0]
$spotify_secret = $spotify_creds[1]

$modal_toml = Get-Content "$env:USERPROFILE\.modal.toml" -Raw
$modal_id = ([regex]::Match($modal_toml, 'token_id\s*=\s*"([^"]+)"')).Groups[1].Value
$modal_secret = ([regex]::Match($modal_toml, 'token_secret\s*=\s*"([^"]+)"')).Groups[1].Value

$turso_url = "https://vibescape-chandan-keelara.aws-us-east-1.turso.io"
$turso_token = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODc5NTM3MTMsImlkIjoiMDFhMDRhNTgtNTIwMS03NzdiLTk1MjgtZTk0MjBlNjQyZDFjIiwia2lkIjoidTltT0lsc203bVQ2Z1NyTThZNzd5eXYyYmM3bUdid0x1eURveEd6NzFaWSIsInJpZCI6Ijk5NDIwNjhjLTllMWItNDkyZS1hNTBjLWVhYTM2OGE2NGI0MCJ9.nfYevF-xZ9lZnSu82bBZkqUhiaPZEHvCRJyP6CRDOZOh3SLgqYU4Qxbv-OCFhJ1jAQDSpdKqyluX8S9qIF9XDg"

$secrets = @{
    "SPOTIFY_CLIENT_ID"     = $spotify_id
    "SPOTIFY_CLIENT_SECRET" = $spotify_secret
    "MODAL_TOKEN_ID"        = $modal_id
    "MODAL_TOKEN_SECRET"    = $modal_secret
    "TURSO_DATABASE_URL"    = $turso_url
    "TURSO_AUTH_TOKEN"      = $turso_token
}

foreach ($name in $secrets.Keys) {
    $value = $secrets[$name]
    if ([string]::IsNullOrWhiteSpace($value)) {
        Write-Warning "SKIP $name value is empty"
        continue
    }

    & $gcloud secrets describe $name --project=$ProjectId 2>$null | Out-Null
    $exists = ($LASTEXITCODE -eq 0)

    $tempFile = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tempFile, $value, (New-Object System.Text.UTF8Encoding($false)))
        if ($exists) {
            Write-Host "  UPDATE $name (adding new version)"
            & $gcloud secrets versions add $name --data-file=$tempFile --project=$ProjectId | Out-Null
        } else {
            Write-Host "  CREATE $name"
            & $gcloud secrets create $name --data-file=$tempFile --replication-policy=automatic --project=$ProjectId | Out-Null
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "  FAILED for $name exit $LASTEXITCODE"
        } else {
            $len = $value.Length
            Write-Host "  OK $name len=$len"
        }
    } finally {
        Remove-Item $tempFile -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "Done. Listing project secrets:"
& $gcloud secrets list --project=$ProjectId 2>&1 | Select-Object -First 15
