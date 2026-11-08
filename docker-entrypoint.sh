#!/bin/sh
set -e

# Run initial backup if enabled (default: true)
if [ "${RUN_ON_STARTUP:-true}" = "true" ] || [ "${RUN_ON_STARTUP:-true}" = "1" ]; then
    echo "[Entrypoint] Running initial backup on startup..."
    python /app/main.py || echo "[Entrypoint] Initial backup encountered an error, will continue with schedule."
fi

SCHEDULE=${CRON_SCHEDULE:-"0 3 * * *"}
echo "[Entrypoint] Setting up cron schedule: $SCHEDULE"
echo "$SCHEDULE python /app/main.py" > /app/crontab

exec "$@"
