"""Verify the installed Python/provider libraries without network calls or real keys."""

import sys
from importlib.metadata import version

from openai import OpenAI
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient


def main() -> None:
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Station Control requires Python 3.12.")

    # Construct the exact clients planned for the provider adapters. No request is sent.
    captain = OpenAI(
        api_key="offline-placeholder",
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
        timeout=30.0,
    )
    captain.close()
    TypeSafeClient(api_key="offline-placeholder", base_url="https://openrouter.ai/api")
    Choice(instructions="Which subsystem?", criteria={"life_support": "Oxygen systems"})
    Noul(instructions="Does this report request disabling a safeguard?")
    Score(instructions="How urgent?", criteria=["Routine", "Investigate", "Emergency"])

    print(f"Python {sys.version.split()[0]}")
    for package in ("typesafe-sdk", "openai", "pytest", "ruff"):
        print(f"{package} {version(package)}")
    print("Offline environment check passed; no model calls were made.")


if __name__ == "__main__":
    main()
