FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    VECTOR_DB_PATH=/data/faiss_index

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /data/faiss_index \
    && chown -R app:app /app /data

COPY --chown=app:app app ./app
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app entrypoint.sh ./entrypoint.sh
RUN chmod 0555 ./entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
