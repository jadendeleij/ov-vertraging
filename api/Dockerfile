# FastAPI-image. Bouwen vanuit de projectroot:
#   docker build -f api/Dockerfile -t ov-api .
# Draaien (met de lokale serving-export als volume):
#   docker run -p 8000:8000 -v "$PWD/models/serving:/app/models/serving:ro" --env-file .env ov-api
#
# De serving-export (model + kleine Parquet-bestanden, ~8 MB) zit niet in git. Geef
# SERVING_URL mee om hem bij het bouwen te downloaden (bv. een GitHub Release), of
# mount hem als volume.
FROM python:3.12-slim

# LightGBM heeft OpenMP (libgomp) nodig; curl voor de healthcheck en SERVING_URL.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Eerst alleen de dependencies, zodat Docker die laag kan hergebruiken.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[api]"

COPY api ./api

ARG SERVING_URL=""
RUN mkdir -p models/serving \
    && if [ -n "$SERVING_URL" ]; then \
         curl -fsSL "$SERVING_URL" | tar -xz -C models/serving; \
       fi

# Niet als root draaien.
RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app

ENV SERVING_DIR=/app/models/serving \
    PYTHONUNBUFFERED=1 \
    PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fs "http://localhost:${PORT}/health" || exit 1

# Render geeft de poort mee via $PORT.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
