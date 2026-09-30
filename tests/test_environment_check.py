"""Exercise credential checking through its command-line interface."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = PROJECT_ROOT / "scripts" / "check_environment.py"


def run_check(key: str | None, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("OPENROUTER_API_KEY", None)
    if key is not None:
        environment["OPENROUTER_API_KEY"] = key
    return subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_offline_setup_succeeds_without_credentials() -> None:
    result = run_check(None)
    assert result.returncode == 0, result.stderr
    assert "OPENROUTER_API_KEY: not configured" in result.stdout
    assert "no model calls were made" in result.stdout


@pytest.mark.parametrize("key", [None, "", "   "])
def test_required_credential_check_rejects_missing_or_blank_key(key: str | None) -> None:
    result = run_check(key, "--require-api-key")
    assert result.returncode == 1
    assert "OPENROUTER_API_KEY is required" in result.stderr


@pytest.mark.parametrize("arguments", [(), ("--require-api-key",)])
def test_injected_key_is_accepted_without_exposing_it(arguments: tuple[str, ...]) -> None:
    secret = "test-secret-that-must-not-appear-in-output"
    result = run_check(secret, *arguments)
    assert result.returncode == 0, result.stderr
    assert "OPENROUTER_API_KEY: configured" in result.stdout
    assert "no model calls were made" in result.stdout
    assert secret not in result.stdout + result.stderr
