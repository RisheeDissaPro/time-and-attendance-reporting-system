#!/bin/sh
# ETL container entrypoint: bootstrap data, run the ETL once, then hand over to cron.
set -eu

cd /app
DATA_DIR="${DATA_DIR:-/data}"
ETL_CRON_SCHEDULE="${ETL_CRON_SCHEDULE:-0 2 * * *}"
TZ="${TZ:-UTC}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') INFO     entrypoint: $*"
}

if [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
fi

if [ ! -f "$DATA_DIR/attendance_export.csv" ]; then
    log "No attendance export in $DATA_DIR; generating sample data"
    python -m db.generate_sample_data --out-dir "$DATA_DIR"
fi

log "Loading reference data and running the initial ETL"
if ! { python -m db.seed && python -m etl.run_etl; }; then
    log "Initial run failed (see errors above); cron will retry on schedule"
fi

# cron starts jobs with an empty environment, so persist the settings they need.
python - <<'EOF'
import os
import shlex

prefixes = ("POSTGRES_", "DATABASE_URL", "OVERTIME_", "ROSTER_", "ATTENDANCE_",
            "DATA_DIR", "LOG_LEVEL", "DB_CONNECT_", "TZ", "PATH", "PYTHON")
with open("/app/cron.env", "w", encoding="utf-8") as handle:
    for key, value in sorted(os.environ.items()):
        if key.startswith(prefixes):
            handle.write(f"export {key}={shlex.quote(value)}\n")
EOF
chmod 600 /app/cron.env

sed "s|__SCHEDULE__|$ETL_CRON_SCHEDULE|" /app/etl/crontab.template > /etc/cron.d/attendance-etl
chmod 0644 /etc/cron.d/attendance-etl

log "Scheduling ETL with cron: '$ETL_CRON_SCHEDULE' ($TZ)"
exec cron -f
