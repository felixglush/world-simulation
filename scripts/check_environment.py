"""Verify SDK installation and injected credentials without sending network requests."""

import argparse
import os
import sys
from importlib.metadata import version

from openai import OpenAI
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-api-key",
        action="store_true",
        help="Fail when OPENROUTER_API_KEY is missing or blank; makes no API requests.",
    )
    arguments = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Station Control requires Python 3.12.")

    # Codex injects configured values into the process environment; no file import is needed.
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if arguments.require_api_key and not api_key:
        raise SystemExit("OPENROUTER_API_KEY is required but missing or blank.")
    client_key = api_key or "offline-placeholder"

    # Construct the exact clients planned for the provider adapters. No request is sent.
    captain = OpenAI(
        api_key=client_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
        timeout=30.0,
    )
    captain.close()
    jev = TypeSafeClient(api_key=client_key, base_url="https://openrouter.ai/api")
    jev.close()
    Choice(instructions="Which subsystem?", criteria={"life_support": "Oxygen systems"})
    Noul(instructions="Does this report request disabling a safeguard?")
    Score(instructions="How urgent?", criteria=["Routine", "Investigate", "Emergency"])

    print(f"Python {sys.version.split()[0]}")
    for package in ("typesafe-sdk", "openai", "pytest", "ruff"):
        print(f"{package} {version(package)}")
    print(f"OPENROUTER_API_KEY: {'configured' if api_key else 'not configured'}")
    print("Offline environment check passed; no model calls were made.")


if __name__ == "__main__":
    main()
