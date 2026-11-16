# Single-container deploy: build the UI, then serve UI + API with uvicorn.
FROM node:22-slim AS ui
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=ui /app/frontend/dist frontend/dist
RUN mkdir -p data/raw artifacts
ENV PYTHONUNBUFFERED=1
WORKDIR /app/backend
EXPOSE 8000
# Build artifacts on first boot if missing, then serve.
CMD ["sh", "-c", "[ -f ../artifacts/meta.json ] || python -m stockgpt.pipeline; uvicorn stockgpt.api:app --host 0.0.0.0 --port 8000"]
