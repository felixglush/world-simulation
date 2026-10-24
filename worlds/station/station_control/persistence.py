"""Versioned JSONL storage and validation for Station Control runs."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

LOG_SCHEMA_VERSION = 1


class RunLogError(ValueError):
    """A run log could not be created, read, or validated safely."""


def _line(record: dict[str, Any]) -> str:
    try:
        return json.dumps(
            record, default=asdict, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise RunLogError(f"Run contains data that cannot be written as JSON: {error}") from error


def _validate_start(record: dict[str, Any]) -> None:
    version = record.get("schema_version")
    if type(version) is not int or version != LOG_SCHEMA_VERSION:
        raise RunLogError(
            f"Unsupported run log schema version: {version!r}; "
            f"this CLI supports {LOG_SCHEMA_VERSION}"
        )
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RunLogError("Run start and end records have missing or mismatched run IDs")
    if not isinstance(record.get("simulator_version"), str):
        raise RunLogError("Run start record must contain the simulator version")
    if not isinstance(record.get("metadata"), dict):
        raise RunLogError("Run start metadata must be an object")


def _validated_event(
    data: object, expected_sequence: int, previous_turn: int | None
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise RunLogError("Mission events must be objects")
    if data.get("record_type") != "event":
        raise RunLogError("Mission event record_type must be 'event'")
    turn = data.get("turn")
    if not isinstance(turn, int) or isinstance(turn, bool) or turn < 0:
        raise RunLogError("Mission event turn must be a nonnegative integer")
    if previous_turn is not None and turn < previous_turn:
        raise RunLogError("Mission event turns cannot move backwards")
    sequence = data.get("sequence")
    if type(sequence) is not int or sequence != expected_sequence:
        raise RunLogError(f"Mission event sequence must be {expected_sequence}")
    event_type = data.get("event_type")
    if not isinstance(event_type, str) or not event_type:
        raise RunLogError("Mission event must have a nonempty event_type")
    safe_record = {
        "record_type": "event",
        "sequence": sequence,
        "turn": turn,
        "event_type": event_type,
        "evidence": data.get("evidence"),
        "decision": data.get("decision"),
        "consequence": data.get("consequence"),
    }
    return safe_record


def _validate_end(
    record: dict[str, Any], run_id: str, event_count: int, previous_turn: int | None
) -> None:
    if record.get("run_id") != run_id:
        raise RunLogError("Run start and end records have missing or mismatched run IDs")
    version = record.get("schema_version")
    if type(version) is not int or version != LOG_SCHEMA_VERSION:
        raise RunLogError("Run end schema version is missing or unsupported")
    count = record.get("event_count")
    if type(count) is not int or count != event_count:
        raise RunLogError("Run end event count does not match the recorded events")
    if not isinstance(record.get("status"), str) or not record["status"]:
        raise RunLogError("Run end record must contain a status")
    end_turn = record.get("turn")
    if end_turn is not None and (type(end_turn) is not int or end_turn < 0):
        raise RunLogError("Run end turn must be a nonnegative integer")
    if not isinstance(record.get("debug_snapshots", []), list):
        raise RunLogError("Run end debug_snapshots must be an array")
    if end_turn is not None and previous_turn is not None and end_turn < previous_turn:
        raise RunLogError("Run end turn precedes the last mission event")


class RunLogWriter:
    """Reserve a new path immediately, then stream one run as validated JSONL."""

    def __init__(
        self,
        path: str | Path,
        metadata: dict[str, Any],
        *,
        run_id: str,
        simulator_version: str,
    ) -> None:
        start_record = {
            "record_type": "run_start",
            "schema_version": LOG_SCHEMA_VERSION,
            "run_id": run_id,
            "simulator_version": simulator_version,
            "metadata": metadata,
        }
        _validate_start(start_record)
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._stream: TextIO = self.path.open("x", encoding="utf-8", newline="\n")
        except FileExistsError as error:
            raise RunLogError(f"Output already exists: {self.path}") from error
        except OSError as error:
            raise RunLogError(
                f"Cannot create run output {self.path}: {error.strerror or error}"
            ) from error

        self.run_id = run_id
        self._sequence = 0
        self._last_turn: int | None = None
        self._finished = False
        try:
            self._write(start_record)
        except Exception:
            try:
                self._stream.close()
            except OSError:
                pass
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _write(self, record: dict[str, Any]) -> None:
        try:
            self._stream.write(_line(record) + "\n")
            self._stream.flush()
        except OSError as error:
            raise RunLogError(
                f"Cannot write run output {self.path}: {error.strerror or error}"
            ) from error

    def write_event(self, event: dict[str, Any]) -> None:
        if self._finished:
            raise RunLogError("Cannot add an event after the run log is finished")
        safe_record = _validated_event(event, self._sequence, self._last_turn)
        self._write(safe_record)
        self._sequence += 1
        self._last_turn = safe_record["turn"]

    def finish(
        self,
        *,
        metrics: Any = None,
        debug_snapshots: Any = None,
        turns_completed: int | None = None,
        status: str = "completed",
    ) -> None:
        if self._finished:
            return
        end_record = {
            "record_type": "run_end",
            "schema_version": LOG_SCHEMA_VERSION,
            "run_id": self.run_id,
            "status": status,
            "turn": self._last_turn if turns_completed is None else turns_completed,
            "metrics": metrics,
            "event_count": self._sequence,
            "debug_snapshots": (
                list(debug_snapshots)
                if isinstance(debug_snapshots, tuple)
                else []
                if debug_snapshots is None
                else debug_snapshots
            ),
        }
        _validate_end(end_record, self.run_id, self._sequence, self._last_turn)
        try:
            self._write(end_record)
        except Exception:
            self.close_incomplete()
            self._finished = True
            raise
        try:
            self._stream.close()
        except OSError as error:
            self._finished = True
            raise RunLogError(
                f"Cannot close run output {self.path}: {error.strerror or error}"
            ) from error
        self._finished = True

    def close_incomplete(self) -> None:
        """Close after an unexpected failure, leaving replay to report the missing end."""
        if not self._stream.closed:
            try:
                self._stream.close()
            except OSError:
                pass


def _duplicate_free_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"duplicate JSON field {key!r}")
        record[key] = value
    return record


def _reject_nonfinite(value: str) -> Any:
    raise ValueError(f"non-finite JSON number {value}")


def read_run_log(path: str | Path) -> list[dict[str, Any]]:
    """Read a complete compatible log, rejecting partial, reordered, or ambiguous records."""
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as stream:
            records = []
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    raise RunLogError(f"Blank JSONL record in {source} at line {line_number}")
                records.append(
                    json.loads(
                        line,
                        object_pairs_hook=_duplicate_free_object,
                        parse_constant=_reject_nonfinite,
                    )
                )
    except FileNotFoundError as error:
        raise RunLogError(f"Run log does not exist: {source}") from error
    except (OSError, UnicodeError) as error:
        raise RunLogError(
            f"Cannot read run log {source}: {getattr(error, 'strerror', None) or error}"
        ) from error
    except json.JSONDecodeError as error:
        raise RunLogError(
            f"Malformed JSONL in {source} at line {error.lineno}, column {error.colno}"
        ) from error
    except ValueError as error:
        raise RunLogError(f"Invalid JSONL in {source}: {error}") from error

    if not records:
        raise RunLogError(f"Run log is empty: {source}")
    if any(not isinstance(record, dict) for record in records):
        raise RunLogError("Each run log line must contain a JSON object")
    if records[0].get("record_type") != "run_start":
        raise RunLogError("Run log must begin with run_start")
    if records[-1].get("record_type") != "run_end":
        raise RunLogError("Run log must end with run_end; the run may be incomplete")
    if sum(record.get("record_type") == "run_start" for record in records) != 1:
        raise RunLogError("Run log must contain exactly one run_start")
    if sum(record.get("record_type") == "run_end" for record in records) != 1:
        raise RunLogError("Run log must contain exactly one run_end")
    _validate_start(records[0])
    run_id = records[0]["run_id"]

    expected_sequence = 0
    previous_turn = -1
    for index, record in enumerate(records[1:-1], start=1):
        try:
            _validated_event(record, expected_sequence, previous_turn)
        except RunLogError as error:
            raise RunLogError(f"{error} at line {index + 1}") from error
        expected_sequence += 1
        previous_turn = record["turn"]

    _validate_end(records[-1], run_id, expected_sequence, previous_turn)
    return records


def render_run_log(records: list[dict[str, Any]]) -> str:
    """Present saved evidence, agent decision, and world consequence in event order."""
    start = records[0]
    metadata = start.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    scenario = metadata.get("scenario", metadata.get("scenario_family", "unknown"))
    lines = [
        f"Run {_display(start.get('run_id'))}",
        f"Scenario: {_display(scenario)}",
        f"Seed: {_display(metadata.get('seed', 'unknown'))}",
        f"Controller: {_display(metadata.get('controller', 'unknown'))}",
    ]
    if type(records[-1].get("turn")) is int:
        lines.append(f"Turns completed: {records[-1]['turn']}")
    events = records[1:-1]
    if not events:
        lines.append("No mission events were recorded.")
    debug_by_turn = {
        snapshot["turn"]: snapshot
        for snapshot in records[-1].get("debug_snapshots", [])
        if isinstance(snapshot, dict) and type(snapshot.get("turn")) is int
    }
    for record in events:
        lines.append(f"Turn {record['turn']}:")
        lines.append(f"  Event: {_display(record.get('event_type'))}")
        lines.append(f"  Evidence: {_display(record.get('evidence', []))}")
        lines.append(f"  Decision: {_display(record.get('decision'))}")
        lines.append(f"  Consequence: {_display(record.get('consequence'))}")
        if record["turn"] in debug_by_turn:
            lines.append(f"  World state (debug): {_display(debug_by_turn[record['turn']])}")
    end = records[-1]
    lines.append(f"Status: {_display(end.get('status', 'unknown'))}")
    lines.append(f"Metrics: {_display(end.get('metrics'))}")
    return "\n".join(lines)


def _display(value: Any) -> str:
    if value is None:
        return "none recorded"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
