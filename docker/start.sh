#!/bin/sh

CRON_SCHEDULE=${CRON_SCHEDULE:-"*/15 * * * *"}
PYTHON=/usr/local/bin/python3
CRON_FILE=/app/crontab

echo "Installing cron job with schedule: $CRON_SCHEDULE"
printf '%s\n' "$CRON_SCHEDULE cd /app && $PYTHON walita.py --no-open -o /app/srv/index.html >> /proc/1/fd/1 2>&1" > "$CRON_FILE"
crontab "$CRON_FILE"

# Initial run
cd /app
$PYTHON walita.py --no-open -o /app/srv/index.html

# Start the standard cron scheduler
cron

# Start webserver
exec $PYTHON -m http.server 8080 --directory /app/srv
