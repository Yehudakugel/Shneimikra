#!/bin/sh
# Builds the SQLite DB with demo data on first boot (Render free disks are ephemeral), then serves on $PORT.
cd "$(dirname "$0")"
mkdir -p data
if [ ! -f data/app.db ]; then python3 seed.py; fi
exec python3 -m gunicorn -w 1 --threads 8 -b 0.0.0.0:${PORT:-10000} --access-logfile - --error-logfile - app:app
