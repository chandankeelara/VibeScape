#!/bin/sh
set -e

# Seed the SQLite DB into the persistent volume on first boot.
# On Fly, /app/data is a mounted volume that survives redeploys.
# The image bakes a copy at /app/seed/vibescape.db — we only copy it
# in if the volume is empty, so subsequent redeploys don't clobber
# user data.
mkdir -p /app/data
if [ ! -f /app/data/vibescape.db ] && [ -f /app/seed/vibescape.db ]; then
    echo "[entrypoint] seeding /app/data/vibescape.db from image"
    cp /app/seed/vibescape.db /app/data/vibescape.db
fi

exec "$@"
