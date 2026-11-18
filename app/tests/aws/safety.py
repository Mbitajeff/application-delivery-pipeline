"""
Safety checks for the AWS Emulator test suite.

These run before any emulator test touches AWS. They make the suite FAIL
(not skip) if it could possibly be talking to real AWS:

  - AWS_ENDPOINT_URL must be set, use https, and point at a loopback host
  - the endpoint each boto3 client actually resolved must be loopback too
    (catches endpoint overrides hidden in a config file)
  - real-credential variables must not be present in the environment
"""

import os
from urllib.parse import urlparse

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
FORBIDDEN_ENV_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
)


class UnsafeEmulatorConfigError(RuntimeError):
    """Raised when the environment could send test traffic to real AWS."""


def assert_loopback_endpoint(endpoint_url: str | None, *, source: str) -> None:
    if not endpoint_url:
        raise UnsafeEmulatorConfigError(
            f"{source} is not set; refusing to run emulator tests without an explicit "
            "local endpoint (expected https://127.0.0.1:9297)."
        )
    parsed = urlparse(endpoint_url)
    if parsed.scheme != "https":
        raise UnsafeEmulatorConfigError(f"{source} must use https, got {parsed.scheme!r}.")
    if parsed.hostname not in LOOPBACK_HOSTS:
        raise UnsafeEmulatorConfigError(
            f"{source} host {parsed.hostname!r} is not localhost; refusing to run "
            "emulator tests against a non-local endpoint."
        )


def assert_no_real_credentials(environ: dict[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    present = [name for name in FORBIDDEN_ENV_VARS if name in env]
    if present:
        # Names only: never echo values.
        raise UnsafeEmulatorConfigError(
            "These variables must be unset for emulator tests: " + ", ".join(present)
        )


def assert_safe_environment(environ: dict[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    assert_loopback_endpoint(env.get("AWS_ENDPOINT_URL"), source="AWS_ENDPOINT_URL")
    assert_no_real_credentials(env)
