# One image holds the API and the React build. The client secret is not in here:
# it arrives through the environment, so a new secret needs a restart, not a build.
FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.9.28
WORKDIR /app/backend
# Dependencies first, from the lock file, so a code change does not reinstall them.
COPY backend/pyproject.toml backend/uv.lock ./
RUN UV_PYTHON_DOWNLOADS=never uv sync --frozen --no-dev --no-install-project --no-cache
COPY backend/app ./app
COPY backend/specs ./specs
COPY --from=frontend /src/dist /app/frontend/dist

# Python tools run code that users paste in, so the process is not root and owns only /data.
RUN useradd --system --uid 10001 --home-dir /data agents && mkdir /data && chown agents /data
USER agents

ENV DATA_DIR=/data \
    DOWNLOADS_DIR=/data/downloads \
    AGENTS_FRONTEND_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1

EXPOSE 8000
# The callback's query string holds the authorization code, so uvicorn's access log stays off.
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
