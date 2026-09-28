FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY apps/api /app/apps/api
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/apps/api/requirements-postgres.lock \
    && python -m pip install /app/apps/api --no-deps \
    && useradd --create-home --uid 10001 labjarvis \
    && mkdir -p /app/.labjarvis /data \
    && chown -R labjarvis:labjarvis /app/.labjarvis /data

USER labjarvis
EXPOSE 8000
CMD ["sh", "-c", "python -m alembic -c apps/api/alembic.ini upgrade head && python -m uvicorn labjarvis.api.app:create_app --factory --host 0.0.0.0 --port 8000"]
