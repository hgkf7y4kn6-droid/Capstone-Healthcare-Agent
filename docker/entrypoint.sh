#!/bin/sh
# Runs once per container startup: if the patient DB doesn't exist yet
# (e.g. first run against a fresh volume), seed it automatically so the
# app is immediately usable without a manual `make ingest` step. Safe to
# run every time — data/ingest_patient_data.py is idempotent — but we skip
# it when the DB is already there to keep container restarts fast.
set -e

if [ ! -f "/app/data/patients.db" ]; then
    echo "[entrypoint] No existing patient DB found — running initial data ingestion..."
    python -m data.ingest_patient_data
else
    echo "[entrypoint] Existing patient DB found — skipping ingestion (data/patients.db present)."
fi

exec "$@"
