import json

import pytest

from bakery_world import cli
from bakery_world.llm import FakeChatModel, Reply, tool_call


def fake_models(monkeypatch) -> list[str]:
    names: list[str] = []

    def make(name: str) -> FakeChatModel:
        names.append(name)
        return FakeChatModel(lambda m, t: Reply(None, (tool_call("view_dashboard"),)), name=name)

    monkeypatch.setattr(cli, "chat_model", make)
    return names


def test_a_budget_stop_still_prints_the_score_report(seed1_history, tmp_path, monkeypatch, capsys):
    names = fake_models(monkeypatch)
    history = str(seed1_history.records.parent)
    argv = ["manage", "--history", history, "--policy", "llm", "--weeks", "3"]
    assert cli.main(argv + ["--max-calls", "12", "--out", str(tmp_path / "run")]) == 0
    out = capsys.readouterr().out
    assert names == ["openai/gpt-5.6-terra", "openai/gpt-6-luna"]
    assert "stopped early: call budget exhausted" in out
    assert "weeks run 1 of 3" in out and "score llm: $" in out and "founder" in out
    assert "model calls 12 (manager 12, analyst 0)" in out
    log = [json.loads(line) for line in (tmp_path / "run" / "run.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in log if r["kind"] in ("stopped", "finish")] == ["stopped", "finish"]


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--policy", "llm"], "--policy llm needs a finite --max-calls"),
        (["--policy", "llm", "--max-calls", "0"], "outside 1..100000"),
        (["--policy", "heuristic", "--max-calls", "5"], "apply only to --policy llm"),
    ],
)
def test_manage_arguments_are_checked(seed1_history, tmp_path, capsys, extra, message) -> None:
    history = str(seed1_history.records.parent)
    with pytest.raises(SystemExit) as stop:
        cli.main(["manage", "--history", history, "--out", str(tmp_path)] + extra)
    assert stop.value.code == 2 and message in capsys.readouterr().err


def test_manage_needs_a_history(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit):
        cli.main(["manage", "--history", str(tmp_path), "--policy", "freeze", "--out", "x"])
    assert "run history first" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--text", "model"], "--text model needs --max-calls"),
        (["--max-calls", "5"], "apply only to --text model"),
    ],
)
def test_history_text_arguments_are_checked(tmp_path, capsys, extra, message) -> None:
    with pytest.raises(SystemExit):
        cli.main(["history", "--seed", "1", "--out", str(tmp_path)] + extra)
    assert message in capsys.readouterr().err
