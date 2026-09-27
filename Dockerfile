# ---------- stage 1: build the React app (frontend-next) ----------
# Node exists only in this stage; the final image stays torch-free AND
# node-free. Cloud Build handles multi-stage fine with `--source .`, so
# deploy/cloud-run/deploy.ps1 needs no changes.
FROM node:20-slim AS fe

WORKDIR /fe
COPY frontend-next/package*.json ./
RUN npm ci
COPY frontend-next/ ./
RUN npm run build


# ---------- stage 2: the runtime image ----------
FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.104.0" \
    "uvicorn[standard]>=0.24.0" \
    "requests>=2.31.0" \
    "modal>=0.63.0" \
    "python-dotenv>=1.0.0" \
    "numpy>=1.24"

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY ingest/ /app/ingest/
COPY config.py /app/config.py
COPY schema.sql /app/schema.sql

# Built React SPA from stage 1 — served at /next by backend/app.py.
COPY --from=fe /fe/dist /app/frontend-next/dist

COPY data/vibescape.db /app/seed/vibescape.db

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

WORKDIR /app/backend

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
# Cloud Run injects $PORT=8080; local runs honor the ENV PORT=8000 default.
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
