# Streamlit-image (online stand: alleen 'Voorspel mijn reis' en 'Live vertrekken').
# Bouwen vanuit de projectroot:
#   docker build -f app/Dockerfile -t ov-app .
#   docker run -p 8501:8501 -e API_URL=http://host.docker.internal:8000 ov-app
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[app]"

COPY app ./app

RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app

# Geen database in de container -> het dashboard kiest de online stand.
ENV API_URL=http://localhost:8000 \
    OV_DUCKDB_PATH=/app/geen-database.duckdb \
    PYTHONUNBUFFERED=1 \
    PORT=8501
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fs "http://localhost:${PORT}/_stcore/health" || exit 1

CMD ["sh", "-c", "streamlit run app/dashboard.py --server.headless true --server.address 0.0.0.0 --server.port ${PORT}"]
