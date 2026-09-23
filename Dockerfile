FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home tirek \
    && mkdir /data && chown tirek:tirek /data
COPY backend/requirements*.txt backend/
COPY model/forecast_v2/requirements.txt model/forecast_v2/
RUN pip install --no-cache-dir -r backend/requirements-ml.txt
COPY backend/ backend/
COPY contracts/ contracts/
COPY model/ model/
COPY scripts/generate_sample_data.py scripts/generate_sample_data.py
COPY --from=frontend /build/dist frontend/dist/
USER tirek
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)" || exit 1
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
