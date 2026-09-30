"""Model selection behavior at the Station Control command line boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from station_control import cli
from station_control.adversary import AdversaryAction, AdversaryActionKind, AdversaryDecision
from station_control.controllers import (
    ActionRequest,
    ActionRequestKind,
    CaptainDecision,
    DispatchJudgment,
    NoulOutcome,
    Subsystem,
)
from station_control.persistence import read_run_log

ROLES = ("captain", "jev", "adversary")


def _install_fakes(monkeypatch) -> dict[str, list[str]]:
    models = {role: [] for role in ROLES}

    def provider_type(role: str):
        class FakeProvider:
            def __init__(self, **options: Any) -> None:
                models[role].append(options["model"])

            def decide(self, _context: Any):
                if role == "adversary":
                    return AdversaryDecision(AdversaryAction(AdversaryActionKind.WAIT))
                return CaptainDecision(
                    ActionRequest(ActionRequestKind.INSPECT, target="oxygen_system")
                )

            def classify(self, _context: Any) -> DispatchJudgment:
                return DispatchJudgment(
                    Subsystem.UNKNOWN, NoulOutcome.NO, NoulOutcome.NO, urgency=0
                )

            def close(self) -> None:
                pass

        return FakeProvider

    for role, name in zip(
        ROLES,
        ("OpenRouterCaptainProvider", "JevDispatchProvider", "OpenRouterAdversaryProvider"),
    ):
        monkeypatch.setattr(cli, name, provider_type(role))
    return models


def _enable_all(output: Path) -> list[str]:
    return [
        "--controller",
        "jev+llm",
        "--adversary",
        "llm",
        "--max-calls",
        "4",
        "--max-output-tokens-per-call",
        "48",
        "--turns",
        "1",
        "--output",
        str(output),
    ]


def _flags(values: dict[str, str]) -> list[str]:
    return [item for role, value in values.items() for item in (f"--{role}-model", value)]


def _logged_models(path: Path) -> dict[str, str | None]:
    return read_run_log(path)[0]["metadata"]["models"]


def _set_environment(monkeypatch, prefix: str) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-test-key")
    for role in ROLES:
        monkeypatch.setenv(f"{role.upper()}_MODEL", f"{prefix}/{role}")


@pytest.mark.parametrize("environment_models", [False, True])
def test_cli_flags_select_enabled_models_and_override_environment(
    tmp_path: Path, monkeypatch, environment_models: bool
) -> None:
    if environment_models:
        _set_environment(monkeypatch, "environment")
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake-test-key")
        for role in ROLES:
            monkeypatch.delenv(f"{role.upper()}_MODEL", raising=False)
    models = _install_fakes(monkeypatch)
    expected = {role: f"flag/{role}" for role in ROLES}
    output = tmp_path / f"explicit-{environment_models}.jsonl"

    assert cli.main(["run", *_enable_all(output), *_flags(expected)]) == 0

    assert models == {role: [model] for role, model in expected.items()}
    assert _logged_models(output) == expected


def test_environment_is_fallback_and_model_flags_do_not_enable_roles(
    tmp_path: Path, monkeypatch
) -> None:
    _set_environment(monkeypatch, "environment")
    models = _install_fakes(monkeypatch)
    output = tmp_path / "environment-models.jsonl"
    assert cli.main(["run", *_enable_all(output)]) == 0
    expected = {role: f"environment/{role}" for role in ROLES}
    assert models == {role: [model] for role, model in expected.items()}
    assert _logged_models(output) == expected

    for name in ("OPENROUTER_API_KEY", *(f"{r.upper()}_MODEL" for r in ROLES)):
        monkeypatch.delenv(name, raising=False)
    offline_models = _install_fakes(monkeypatch)
    offline = tmp_path / "offline-model-flags.jsonl"
    assert (
        cli.main(
            [
                "run",
                "--controller",
                "rules",
                "--turns",
                "1",
                "--output",
                str(offline),
                *_flags({role: f"flag/{role}" for role in ROLES}),
            ]
        )
        == 0
    )
    assert offline_models == {role: [] for role in ROLES}
    assert _logged_models(offline) == {role: None for role in ROLES}


def test_rerun_uses_current_flag_and_environment_instead_of_saved_models(
    tmp_path: Path, monkeypatch
) -> None:
    _set_environment(monkeypatch, "saved")
    models = _install_fakes(monkeypatch)
    original = tmp_path / "original.jsonl"
    assert cli.main(["run", *_enable_all(original)]) == 0

    _set_environment(monkeypatch, "current")
    rerun = tmp_path / "rerun.jsonl"
    assert (
        cli.main(["rerun", str(original), *_enable_all(rerun), "--captain-model", "rerun/captain"])
        == 0
    )

    expected = {"captain": "rerun/captain", "jev": "current/jev", "adversary": "current/adversary"}
    assert models == {role: [f"saved/{role}", expected[role]] for role in ROLES}
    assert _logged_models(rerun) == expected


@pytest.mark.parametrize("flag", ["--captain-model", "--jev-model", "--adversary-model"])
def test_whitespace_only_model_flag_is_rejected_before_output(
    tmp_path: Path, flag: str, capsys
) -> None:
    output = tmp_path / "blank.jsonl"
    with pytest.raises(SystemExit) as error:
        cli.main(["run", "--controller", "rules", "--output", str(output), flag, " \t "])

    assert error.value.code == 2
    assert "blank" in capsys.readouterr().err.lower()
    assert not output.exists()
