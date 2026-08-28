# Cloud Run deploy runbook

Deploys the VibeScape FastAPI backend to Google Cloud Run using the repo
root `Dockerfile`. Cloud Build handles the image build; Cloud Run hosts
the container.

## Prereqs

- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- A GCP project with billing enabled
- Required APIs enabled:
  ```
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
                         artifactregistry.googleapis.com secretmanager.googleapis.com
  ```
- Modal deployed (`modal deploy modal_app.py`) — need `MODAL_TOKEN_ID` / `_SECRET`
- Spotify Developer app — need `SPOTIFY_CLIENT_ID` / `_SECRET`
- Turso database provisioned (`turso db create vibescape` → note URL + token)

## Secrets in Secret Manager

Create one secret per sensitive value. Cloud Run mounts these as env
vars at runtime — they never touch the container image.

```bash
PROJECT_ID=$(gcloud config get-value project)

for name in SPOTIFY_CLIENT_ID SPOTIFY_CLIENT_SECRET \
            MODAL_TOKEN_ID MODAL_TOKEN_SECRET \
            TURSO_DATABASE_URL TURSO_AUTH_TOKEN; do
  echo "-n $name value:"
  read -s VAL
  echo -n "$VAL" | gcloud secrets create "$name" --data-file=- \
    --replication-policy=automatic 2>/dev/null \
    || echo -n "$VAL" | gcloud secrets versions add "$name" --data-file=-
done
```

Give the Cloud Run runtime service account access:

```bash
SA="$(gcloud projects describe $PROJECT_ID \
    --format='value(projectNumber)')-compute@developer.gserviceaccount.com"

for name in SPOTIFY_CLIENT_ID SPOTIFY_CLIENT_SECRET \
            MODAL_TOKEN_ID MODAL_TOKEN_SECRET \
            TURSO_DATABASE_URL TURSO_AUTH_TOKEN; do
  gcloud secrets add-iam-policy-binding "$name" \
    --member="serviceAccount:$SA" \
    --role="roles/secretmanager.secretAccessor"
done
```

## Deploy

From the repo root:

```bash
gcloud run deploy vibescape \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --timeout 300 \
  --set-env-vars "VIBESCAPE_ML_MODE=modal,DB_BACKEND=turso,SPOTIFY_REDIRECT_URI=https://vibescape-<HASH>-uc.a.run.app/callback" \
  --set-secrets "SPOTIFY_CLIENT_ID=SPOTIFY_CLIENT_ID:latest,SPOTIFY_CLIENT_SECRET=SPOTIFY_CLIENT_SECRET:latest,MODAL_TOKEN_ID=MODAL_TOKEN_ID:latest,MODAL_TOKEN_SECRET=MODAL_TOKEN_SECRET:latest,TURSO_DATABASE_URL=TURSO_DATABASE_URL:latest,TURSO_AUTH_TOKEN=TURSO_AUTH_TOKEN:latest"
```

The first `gcloud run deploy --source .` uploads the repo to Cloud Build,
which reads `Dockerfile`, builds the image, pushes to Artifact Registry,
and hands the resulting image to Cloud Run. Takes ~3-5 min the first
time; ~1-2 min for redeploys (build cache).

After the first deploy, note the URL Cloud Run assigns (of the shape
`https://vibescape-<hash>-uc.a.run.app`), then:

1. Update the deploy command's `SPOTIFY_REDIRECT_URI` to that URL + `/callback`
2. Add the same URL + `/callback` to your Spotify Developer Dashboard's
   allowed redirect URIs
3. Redeploy (`gcloud run deploy vibescape --source .` re-uses cached build)

## Data migration

Before the first Turso-backed deploy, seed the remote DB from your local
SQLite:

```bash
export TURSO_DATABASE_URL=libsql://vibescape-<user>.turso.io
export TURSO_AUTH_TOKEN=<token>

python scripts/migrate_to_turso.py \
  --source data/vibescape.db \
  --target-url "$TURSO_DATABASE_URL" \
  --target-token "$TURSO_AUTH_TOKEN"
```

Verify row counts match, then deploy.

## Smoke test

```bash
URL=$(gcloud run services describe vibescape --region us-central1 \
      --format='value(status.url)')

curl -sS -o - -w "\nHTTP %{http_code}\n" "$URL/api/health"
# Expect: HTTP 200
```

Then open `$URL` in a browser, log in via Spotify, and paste a small
playlist to confirm ingest works end-to-end.

## Rollback

Cloud Run keeps every revision. To roll back:

```bash
gcloud run services describe vibescape --region us-central1 \
  --format="table(status.traffic[].revisionName,status.traffic[].percent)"

gcloud run services update-traffic vibescape --region us-central1 \
  --to-revisions <PREVIOUS_REVISION>=100
```

## Notes

- `--min-instances 0` = scale to zero when idle. Free tier eats this
  for breakfast. Cold start ~1-3 s.
- `--memory 512Mi` matches Fly's config. Bump to 1 GiB if you need
  more headroom (still free-tier eligible).
- `--allow-unauthenticated` makes the URL public. Remove this flag
  to require IAM identity; not what you want for a webapp.
- `--timeout 300` gives ingest requests up to 5 min (Modal calls can
  take a while for long playlists). Max is 3600s.
