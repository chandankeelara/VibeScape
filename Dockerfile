FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.104.0" \
    "uvicorn[standard]>=0.24.0" \
    "requests>=2.31.0" \
    "modal>=0.63.0" \
    "python-dotenv>=1.0.0"

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY ingest/ /app/ingest/
COPY config.py /app/config.py
COPY schema.sql /app/schema.sql

COPY data/vibescape.db /app/seed/vibescape.db

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

WORKDIR /app/backend

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
