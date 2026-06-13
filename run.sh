#!/bin/bash
set -e

# Home Assistant base images run on s6-overlay, which keeps the Supervisor-provided
# environment (including SUPERVISOR_TOKEN) under /run/s6/container_environment
# instead of in the process env. Load it so the optional Wallbox feature can reach
# the HA API as an add-on. (No-op when standalone — the directory won't exist.)
if [ -d /run/s6/container_environment ]; then
  for _f in /run/s6/container_environment/*; do
    [ -f "${_f}" ] && export "$(basename "${_f}")=$(cat "${_f}")"
  done
fi

export DB_PATH="${DB_PATH:-/data/leapmotor_mate.db}"
export CERT_DIR="/app/certs"

# Keep the temporary files the Leapmotor API writes — the per-login account TLS cert + key
# (tempfile.mkstemp, suffix -leapmotor-cert.pem / -leapmotor-key.pem) — on the PERSISTENT /data
# volume instead of the container's ephemeral /tmp. A standalone Docker (e.g. on a NAS) wipes
# /tmp on every restart, so those two files would vanish and remote commands would then fail with
# "Could not find the TLS certificate file" (and every restart would force a fresh re-login). /data
# survives restarts. Guarded: if /data/tmp can't be created, TMPDIR is left as-is (falls back to /tmp).
if mkdir -p /data/tmp 2>/dev/null; then
  export TMPDIR=/data/tmp
fi

echo "[LeapMotor Mate] Starting..."
echo "[LeapMotor Mate] DB: ${DB_PATH}"
echo "[LeapMotor Mate] Home Assistant API: $([ -n "${SUPERVISOR_TOKEN}" ] && echo "available (add-on mode)" || echo "not available (standalone)")"

# Start poller in background
PYTHONPATH=/app/poller python3 /app/poller/main.py &
POLLER_PID=$!
echo "[LeapMotor Mate] Poller PID: ${POLLER_PID}"

# Start web server in background
PYTHONPATH=/app/web python3 /app/web/main.py &
WEB_PID=$!
echo "[LeapMotor Mate] Web PID: ${WEB_PID}"

# If either service exits, stop the container (HA or Docker will restart it)
wait -n "$POLLER_PID" "$WEB_PID"
EXIT_CODE=$?
echo "[LeapMotor Mate] A service exited (code ${EXIT_CODE}) — stopping"
kill "$POLLER_PID" "$WEB_PID" 2>/dev/null
exit "$EXIT_CODE"
