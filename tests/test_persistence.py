"""Writer and replay enforce the same public run-log contract."""

import json

import pytest

from station_control.persistence import RunLogError, RunLogWriter, read_run_log


def _writer(path):
    return RunLogWriter(path, {}, run_id="contract", simulator_version="0.3.1")


@pytest.mark.parametrize("event_type", [None, "", 4])
def test_replay_rejects_event_types_that_the_writer_rejects(tmp_path, event_type):
    path = tmp_path / "run.jsonl"
    writer = _writer(path)
    event = {"record_type": "event", "sequence": 0, "turn": 2, "event_type": "turn"}
    writer.write_event(event)
    writer.finish()
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[1]["event_type"] = event_type
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    with pytest.raises(RunLogError, match="event_type"):
        read_run_log(path)


@pytest.mark.parametrize(
    "settings",
    [
        {"status": ""},
        {"turns_completed": -1},
        {"turns_completed": 1},
        {"turns_completed": True},
        {"debug_snapshots": {"unexpected": "object"}},
    ],
)
def test_invalid_finish_does_not_write_an_unreplayable_end_record(tmp_path, settings):
    path = tmp_path / "run.jsonl"
    writer = _writer(path)
    writer.write_event({"record_type": "event", "sequence": 0, "turn": 2, "event_type": "turn"})
    try:
        with pytest.raises(RunLogError):
            writer.finish(**settings)
        writer.finish(turns_completed=2)
    finally:
        writer.close_incomplete()
    assert read_run_log(path)[-1]["turn"] == 2


@pytest.mark.parametrize("run_id", ["", None])
def test_writer_rejects_invalid_run_identity_before_reserving_a_path(tmp_path, run_id):
    path = tmp_path / "run.jsonl"
    with pytest.raises(RunLogError):
        writer = RunLogWriter(path, {}, run_id=run_id, simulator_version="0.3.1")
        writer.close_incomplete()
    assert not path.exists()
