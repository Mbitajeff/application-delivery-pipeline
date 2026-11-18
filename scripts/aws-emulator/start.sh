#!/usr/bin/env bash
# =============================================================================
# Start AWS Emulator in the background and wait for its health check.
#
#   scripts/aws-emulator/start.sh        # start + wait (default port 9297)
#   scripts/aws-emulator/start.sh stop   # stop it (emulator deletes its files)
#
# - Telemetry is turned off (flag AND env var; "an off setting always wins").
# - Real AWS credential variables are unset for the emulator process.
# - Under GitHub Actions the emulator's generated keys are registered as
#   masked values before anything else is printed, and the SDK file paths
#   (never the keys themselves) are exported to $GITHUB_ENV.
# =============================================================================
set -euo pipefail

PORT="${EMULATOR_PORT:-9297}"
STATE_DIR="${HOME}/.aws/emulator/${PORT}"
RUN_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
PID_FILE="${RUN_DIR}/aws-emulator-${PORT}.pid"
LOG_FILE="${RUN_DIR}/aws-emulator-${PORT}.log"
HEALTH_URL="https://127.0.0.1:${PORT}/__admin/health"
TIMEOUT_S="${EMULATOR_START_TIMEOUT:-60}"

unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE
export EMULATOR_TELEMETRY=off

if [[ "${1:-}" == "stop" ]]; then
  if [[ -f "${PID_FILE}" ]]; then
    kill "$(cat "${PID_FILE}")" 2>/dev/null || true   # SIGTERM: emulator cleans up
    rm -f "${PID_FILE}"
    echo "AWS Emulator stopped."
  fi
  exit 0
fi

# Print the emulator log with anything that looks like a key redacted.
show_log() {
  [[ -f "${LOG_FILE}" ]] || return 0
  echo "---- last 50 lines of emulator log (redacted) ----"
  tail -n 50 "${LOG_FILE}" | sed -E \
    -e 's/(A[KS]IA|ASIA)[A-Z0-9]{12,}/[REDACTED]/g' \
    -e 's/[A-Za-z0-9\/+=]{30,}/[REDACTED]/g'
}

# Register every credential value in the generated file as a masked secret.
mask_credentials() {
  [[ "${GITHUB_ACTIONS:-}" == "true" && -f "${STATE_DIR}/emulator_credentials" ]] || return 0
  while IFS='=' read -r key value; do
    key="$(tr -d '[:space:]' <<<"${key}")"
    value="$(tr -d '[:space:]' <<<"${value}")"
    case "${key}" in
      aws_access_key_id | aws_secret_access_key | aws_session_token)
        if [[ -n "${value}" ]]; then echo "::add-mask::${value}"; fi ;;
    esac
  done <"${STATE_DIR}/emulator_credentials"
  return 0
}

echo "Starting AWS Emulator on 127.0.0.1:${PORT} (telemetry off)..."
nohup aws-emulator --telemetry off -p "${PORT}" >"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"

for ((i = 1; i <= TIMEOUT_S; i++)); do
  if ! kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    echo "ERROR: AWS Emulator exited during startup." >&2
    show_log
    exit 1
  fi
  body=""
  if [[ -f "${STATE_DIR}/ca.pem" ]]; then
    body="$(curl -fsS --max-time 5 --cacert "${STATE_DIR}/ca.pem" "${HEALTH_URL}" 2>/dev/null || true)"
  fi
  if [[ "${body}" == "ok" ]]; then
    mask_credentials
    echo "AWS Emulator healthy after ${i}s: ${HEALTH_URL}"
    if [[ -n "${GITHUB_ENV:-}" ]]; then
      {
        echo "AWS_CA_BUNDLE=${STATE_DIR}/ca.pem"
        echo "AWS_CONFIG_FILE=${STATE_DIR}/config"
        echo "AWS_SHARED_CREDENTIALS_FILE=${STATE_DIR}/emulator_credentials"
      } >>"${GITHUB_ENV}"
    fi
    exit 0
  fi
  sleep 1
done

echo "ERROR: AWS Emulator not healthy after ${TIMEOUT_S}s." >&2
mask_credentials
show_log
exit 1
