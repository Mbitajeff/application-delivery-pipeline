# shellcheck shell=bash
# =============================================================================
# Point the AWS SDK/CLI at a local AWS Emulator for the current shell.
#
#   source scripts/aws-emulator/env.sh
#
# Variables follow the AWS docs ("Pointing your AWS SDK at AWS Emulator").
# Real credential variables are unset so nothing can reach real AWS.
# =============================================================================
_EMU_PORT="${EMULATOR_PORT:-9297}"
_EMU_DIR="${HOME}/.aws/emulator/${_EMU_PORT}"

unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE

export AWS_ENDPOINT_URL="https://127.0.0.1:${_EMU_PORT}"
export AWS_SHARED_CREDENTIALS_FILE="${_EMU_DIR}/emulator_credentials"
export AWS_CONFIG_FILE="${_EMU_DIR}/config"
export AWS_CA_BUNDLE="${_EMU_DIR}/ca.pem"
export AWS_EC2_METADATA_DISABLED=true
export AWS_DISABLE_HOST_PREFIX_INJECTION=true
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export EMULATOR_TELEMETRY=off

unset _EMU_PORT _EMU_DIR
echo "AWS SDK now points at ${AWS_ENDPOINT_URL} (AWS Emulator)."
