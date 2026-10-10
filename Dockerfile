# Relay: one container serves the API and the built frontend.
# Stage 1: build the React frontend.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime.
FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 RELAY_ENV=local
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY config.yaml ./
COPY engine/ engine/
COPY api/ api/
COPY data/kit/ data/kit/
COPY data/demo_events/ data/demo_events/
COPY --from=web /web/dist frontend/dist
RUN useradd --create-home relay && mkdir -p data/state && chown -R relay data/state
USER relay
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/api/health')"
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
