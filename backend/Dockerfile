# Revora backend. BUILD_SPEC Section 3 (Docker Compose: api + web, shared
# volume for SQLite) and Section 14 (write the skeleton as soon as the backend
# runs locally — session 2 — so build failures surface early, not on the last day).
#
# No Redis, no Kafka, no Postgres. SQLite on a mounted volume is the whole
# persistence story.

FROM python:3.12-slim

# Bytecode files and buffered output both make container logs harder to read.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, as their own layer: application code changes on every
# commit, requirements.txt rarely does, so this keeps rebuilds fast across the
# remaining sessions.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# The SQLite file lives on a volume mounted here, so the database survives
# container restarts (Section 3). docker-compose.yml declares it in session 6-7.
RUN mkdir -p /data
ENV DATABASE_URL=sqlite:////data/revora.db

# Run unprivileged. The app never needs root, and /data must be writable by the
# runtime user or the first write fails at startup rather than at build time.
RUN useradd --create-home --uid 1000 revora && chown -R revora:revora /app /data
USER revora

EXPOSE 8000

# app/main.py is built in session 4, when the routers exist to mount. Until then
# the image builds and the dependency layer is validated; this CMD is what it
# will start with.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
