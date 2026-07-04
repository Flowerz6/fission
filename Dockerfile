# syntax=docker/dockerfile:1
#
# Fission app image — dashboard + CLI. Deliberately SLIM (no browser): the
# browser-farm WORKER image (Stage 2) extends this with Chromium via
#   RUN poetry install --extras ui_mode && playwright install --with-deps chromium
# (~300 MB of Chromium + system libs), which the dashboard does not need.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.2.1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

# Manifests + package + the files the flat-layout path anchors resolve to at
# runtime (references.bib next to the repo root, configs/ under it). Poetry
# installs the root project in editable mode, so advtest_ext keeps living at
# /app/advtest_ext and `Path(__file__).parents[...]` resolves to /app.
COPY pyproject.toml poetry.lock README.md references.bib ./
COPY advtest_ext ./advtest_ext
COPY configs ./configs

RUN poetry install --only main --extras "postgres gcs" --no-interaction

# Drop privileges.
RUN useradd --create-home --uid 10001 fission && chown -R fission:fission /app
USER fission

EXPOSE 8000
# Dashboard by default. Override CMD for the CLI (`advtest ...`) or a worker.
# create_app() reads ADVTEST_DB_URL / ADVTEST_DASHBOARD_TOKEN from the env.
CMD ["uvicorn", "advtest_ext.dashboard:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
