# Post-deploy cleanup: prunes old Cloud Run revisions, old container images
# in Artifact Registry, and stale Secret Manager versions.
#
# Safe to re-run. Keeps the currently-serving revision + 1 prior, the two
# latest container images, and the latest version of each secret.
#
# Usage:
#   .\deploy\cloud-run\cleanup.ps1
#   .\deploy\cloud-run\cleanup.ps1 -KeepRevisions 3 -KeepImages 3
param(
    [string]$Service = "vibescape",
    [string]$Region  = "us-central1",
    [string]$Repo    = "cloud-run-source-deploy",
    [int]$KeepRevisions = 2,
    [int]$KeepImages    = 2
)

$gcloud = "C:\Users\chand\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$Project = (& $gcloud config get-value project 2>$null).Trim()
$Registry = "$Region-docker.pkg.dev/$Project/$Repo/$Service"

Write-Host "Project:  $Project"
Write-Host "Service:  $Service ($Region)"
Write-Host "Registry: $Registry"
Write-Host ""

# --- Cloud Run revisions ---
Write-Host "=== Cloud Run revisions ==="
$revs = & $gcloud run revisions list --service=$Service --region=$Region --format="value(metadata.name)" 2>$null
if ($LASTEXITCODE -eq 0 -and $revs) {
    $revList = @($revs -split "`n" | Where-Object { $_ })
    # Newest first (revision names sort lexically in reverse chronological order)
    $revList = $revList | Sort-Object -Descending
    $toDelete = $revList | Select-Object -Skip $KeepRevisions
    if ($toDelete) {
        foreach ($r in $toDelete) {
            Write-Host "  DELETE revision $r"
            & $gcloud run revisions delete $r --region=$Region --quiet 2>&1 | Select-Object -Last 2
        }
    } else {
        Write-Host "  nothing to delete (revisions: $($revList.Count), keep: $KeepRevisions)"
    }
}
Write-Host ""

# --- Artifact Registry images ---
Write-Host "=== Artifact Registry images ==="
$digests = & $gcloud artifacts docker images list $Registry --sort-by="~UPDATE_TIME" --format="value(DIGEST)" --include-tags 2>$null
if ($LASTEXITCODE -eq 0 -and $digests) {
    $digestList = @($digests -split "`n" | Where-Object { $_ })
    $toDelete = $digestList | Select-Object -Skip $KeepImages
    if ($toDelete) {
        foreach ($d in $toDelete) {
            $imageRef = "$Registry@$d"
            Write-Host "  DELETE image $d"
            & $gcloud artifacts docker images delete $imageRef --delete-tags --quiet 2>&1 | Select-Object -Last 2
        }
    } else {
        Write-Host "  nothing to delete (images: $($digestList.Count), keep: $KeepImages)"
    }
}
Write-Host ""

# --- Secret Manager versions ---
Write-Host "=== Secret Manager versions ==="
$secretsToClean = @("SPOTIFY_CLIENT_ID","SPOTIFY_CLIENT_SECRET","MODAL_TOKEN_ID","MODAL_TOKEN_SECRET","TURSO_DATABASE_URL","TURSO_AUTH_TOKEN")
foreach ($sec in $secretsToClean) {
    $versions = & $gcloud secrets versions list $sec --filter="state=ENABLED" --format="value(name)" --sort-by="~createTime" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $versions) { continue }
    $verList = @($versions -split "`n" | Where-Object { $_ })
    if ($verList.Count -le 1) {
        Write-Host "  ${sec}: 1 version, skipping"
        continue
    }
    $toDestroy = $verList | Select-Object -Skip 1
    foreach ($v in $toDestroy) {
        Write-Host "  DESTROY $sec version $v"
        & $gcloud secrets versions destroy $v --secret=$sec --quiet 2>&1 | Select-Object -Last 1
    }
}

Write-Host ""
Write-Host "Cleanup done."
