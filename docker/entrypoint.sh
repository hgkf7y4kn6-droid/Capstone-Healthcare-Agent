#!/bin/sh
# The API's startup hook (data/bootstrap.py) creates the schema and, only in
# DEMO_MODE, loads the synthetic roster. Real deployments import their own
# roster with `python -m data.ingest_patient_data --source <export>`.
set -e
mkdir -p "${DATA_DIR:-/app/data}" "${LOGS_DIR:-/app/logs}"
exec "$@"
