#!/usr/bin/env bash
# =============================================================================
# Install AWS Emulator on Ubuntu/Debian from the official AWS package,
# after verifying its GPG signature.
#
# Source: https://docs.aws.amazon.com/emulator/latest/userguide/getting-started-linux.html
#
# The install is refused unless:
#   1. the detached signature (.sig) verifies against the package, AND
#   2. the signing key's primary fingerprint matches the one AWS documents.
#
# Used by CI (.github/workflows/*.yml) and usable on a local Ubuntu machine.
# =============================================================================
set -euo pipefail

BASE_URL="https://downloads.aws.com/aws-emulator/packages/aws-emulator/LTS/latest"
KEY_URL="https://downloads.aws.com/keys/gpg/current.asc"
# Documented fingerprint for ssm-publishing@amazon.com:
#   98BC F336 A3BE 53B7 34E9 67C5 2E5B F49B D940 F624
EXPECTED_FPR="98BCF336A3BE53B734E967C52E5BF49BD940F624"

ARCH="$(dpkg --print-architecture)"
case "${ARCH}" in
  amd64 | arm64) ;;
  *) echo "Unsupported architecture: ${ARCH} (AWS Emulator .deb supports amd64, arm64)" >&2; exit 1 ;;
esac
PKG="aws-emulator_${ARCH}.deb"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT
export GNUPGHOME="${WORKDIR}/gnupg"
mkdir -m 700 "${GNUPGHOME}"
cd "${WORKDIR}"

fetch() { curl -fsSL --proto '=https' --tlsv1.2 --retry 3 -o "$1" "$2"; }

echo "Downloading ${PKG}, its signature and the AWS publishing key..."
fetch "${PKG}" "${BASE_URL}/${PKG}"
fetch "${PKG}.sig" "${BASE_URL}/${PKG}.sig"
fetch aws-publishing.asc "${KEY_URL}"

gpg --batch --quiet --import aws-publishing.asc
echo "Imported key fingerprint:"
gpg --batch --fingerprint ssm-publishing@amazon.com

# --status-fd gives machine-readable results. VALIDSIG's last field is the
# PRIMARY key fingerprint of the key that made the signature.
STATUS="$(gpg --batch --status-fd 1 --verify "${PKG}.sig" "${PKG}" 2>/dev/null || true)"

if ! grep -q '^\[GNUPG:\] GOODSIG ' <<<"${STATUS}"; then
  echo "ERROR: signature verification FAILED for ${PKG}. Not installing." >&2
  exit 1
fi
SIGNER_FPR="$(awk '$2 == "VALIDSIG" { print $NF }' <<<"${STATUS}")"
if [[ "${SIGNER_FPR}" != "${EXPECTED_FPR}" ]]; then
  echo "ERROR: package signed by unexpected key '${SIGNER_FPR}'," \
       "expected '${EXPECTED_FPR}'. Not installing." >&2
  exit 1
fi
echo "Signature OK (signer ${SIGNER_FPR})."

sudo apt-get install -y "./${PKG}"
echo "Installed: $(aws-emulator -v)"
