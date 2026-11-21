# FastAPI-image. Bouwen vanuit de projectroot:
#   docker build -f api/Dockerfile -t ov-api .
# Draaien (met de lokale serving-export als volume):
#   docker run -p 8000:8000 -v "$PWD/models/serving:/app/models/serving:ro" --env-file .env ov-api
#
# De serving-export (model + kleine Parquet-bestanden) zit niet in git maar in een
# GitHub Release; die wordt bij het bouwen gedownload en gecontroleerd (sha256).
# Nieuw model? `python -m ov_vertraging.serving.export --package`, nieuwe Release, en
# hieronder SERVING_VERSION ophogen. Lokaal kan ook een volume (zie docker-compose.yml).
FROM python:3.12-slim

# LightGBM heeft OpenMP (libgomp) nodig; curl voor de healthcheck en de Release-download.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Eerst alleen de dependencies, zodat Docker die laag kan hergebruiken.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[api]"

COPY api ./api

ARG SERVING_VERSION=20261121
ARG SERVING_BASE=https://github.com/jadendeleij/ov-vertraging/releases/download
RUN mkdir -p models/serving && cd models \
    && if [ "$SERVING_VERSION" != "none" ]; then \
         f="serving-${SERVING_VERSION}.tar.gz"; \
         curl -fsSLO "${SERVING_BASE}/serving-${SERVING_VERSION}/${f}" \
         && curl -fsSLO "${SERVING_BASE}/serving-${SERVING_VERSION}/${f}.sha256" \
         && sha256sum -c "${f}.sha256" \
         && tar -xzf "${f}" -C serving && rm "${f}" "${f}.sha256"; \
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
