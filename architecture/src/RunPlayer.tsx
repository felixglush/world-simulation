import { Button } from "./components/ui/button";
import { Badge } from "./components/ui/badge";
import { useEffect, useRef, useState } from "react";
import {
  Download,
  Pause,
  Play,
  RotateCcw,
  SkipBack,
  SkipForward,
  Upload,
  X,
} from "lucide-react";
import { eventTrace, format, parseRun, worldAt } from "./model";
import type { DemoRun, RunRecord } from "./model";
interface Props {
  run: DemoRun;
  runs: DemoRun[];
  onRun: (run: DemoRun) => void;
  events: RunRecord[];
  cursor: number;
  setCursor: (index: number) => void;
  playing: boolean;
  setPlaying: (playing: boolean) => void;
  onClose: () => void;
}
export function RunPlayer({
  run,
  runs,
  onRun,
  events,
  cursor,
  setCursor,
  playing,
  setPlaying,
  onClose,
}: Props) {
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [all, setAll] = useState(false);
  const [speed, setSpeed] = useState(1);
  const event = events[cursor],
    trace = eventTrace(event);
  const state = worldAt(events, cursor),
    before = worldAt(events, cursor - 1);
  const indices = events
    .map((_, index) => index)
    .filter(
      (index) =>
        all ||
        events[index].event_type !== "world_transition" ||
        ["scheduled_event", "oxygen", "repairs", "captain_action"].includes(
          events[index].consequence?.phase,
        ),
    );
  useEffect(() => {
    if (!playing) return;
    if (cursor >= events.length - 1) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => setCursor(cursor + 1), 1200 / speed);
    return () => clearTimeout(timer);
  }, [playing, cursor, events.length, setCursor, setPlaying, speed]);
  useEffect(() => {
    list.current
      ?.querySelector('[aria-current="step"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor]);
  async function importFile(file?: File) {
    if (!file) return;
    try {
      if (file.size > 5_000_000)
        throw new Error("Use a JSONL run smaller than 5 MB.");
      const imported = parseRun(await file.text(), file.name);
      onRun(imported);
      setError("");
    } catch (error) {
      setError(
        error instanceof Error ? error.message : "Unable to read this run.",
      );
    }
    if (input.current) input.current.value = "";
  }
  function download() {
    const blob = new Blob(
      [run.records.map((record) => JSON.stringify(record)).join("\n") + "\n"],
      { type: "application/x-ndjson" },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${run.id}.jsonl`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="run-player" aria-label="Turn walkthrough">
      <div className="run-toolbar">
        <div className="run-label">
          <span className="live-dot" />
          <strong>Run player</strong>
          <Badge variant="outline" className="demo-badge">
            {run.id.startsWith("import-") ? "Imported run" : "Scripted AI demo"}
          </Badge>
        </div>
        <select
          aria-label="Run input"
          value={run.id}
          onChange={(event) =>
            onRun(runs.find((item) => item.id === event.target.value)!)
          }
        >
          {runs.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </select>
        <Button
          variant="ghost"
          className="text-button"
          onClick={() => input.current?.click()}
        >
          <Upload size={15} />
          Load JSONL
        </Button>
        <input
          type="file"
          accept=".jsonl,.ndjson,.json"
          ref={input}
          onChange={(event) => void importFile(event.target.files?.[0])}
          aria-label="Import run file"
          hidden
        />
        <Button
          variant="ghost"
          className="icon-button"
          aria-label="Download this run"
          title="Download this run"
          onClick={download}
        >
          <Download size={17} />
        </Button>
        <Button
          variant="ghost"
          className="icon-button"
          aria-label="Close run player"
          onClick={onClose}
        >
          <X size={18} />
        </Button>
      </div>
      {error && (
        <div role="alert" className="import-error">
          {error}
        </div>
      )}
      <div className="run-main">
        <div className="event-journal">
          <div className="journal-heading">
            <span>EVENT JOURNAL</span>
            <label>
              <input
                type="checkbox"
                checked={all}
                onChange={(event) => setAll(event.target.checked)}
              />
              All phases
            </label>
          </div>
          <div className="event-list" ref={list}>
            {indices.map((index) => {
              const item = events[index],
                itemTrace = eventTrace(item);
              return (
                <Button
                  variant="ghost"
                  key={item.sequence}
                  className={cursor === index ? "current" : ""}
                  aria-current={cursor === index ? "step" : undefined}
                  onClick={() => {
                    setPlaying(false);
                    setCursor(index);
                  }}
                >
                  <span className="event-index">
                    {String(item.sequence).padStart(2, "0")}
                  </span>
                  <span>
                    <strong>{itemTrace.title}</strong>
                    <small>
                      Turn {item.turn} · {item.event_type}
                    </small>
                  </span>
                  <i
                    className={itemTrace.private ? "private-dot" : "public-dot"}
                  />
                </Button>
              );
            })}
          </div>
        </div>
        <div className="event-stage">
          <div className="event-kicker">
            TURN {event?.turn ?? 0} <span>/ EVENT {event?.sequence ?? 0}</span>
            <b className={trace.private ? "private-badge" : "public-badge"}>
              {trace.private
                ? "Private audit fact"
                : "Crew / application event"}
            </b>
          </div>
          <h3>{trace.title}</h3>
          <p>{trace.body}</p>
          <div className="event-payload">
            <span>Recorded message</span>
            <code>
              {format(event?.decision ?? event?.evidence ?? event?.consequence)}
            </code>
          </div>
        </div>
        <div className="world-metrics">
          <div className="journal-heading">
            WORLD STATE <span>at this event</span>
          </div>
          {[
            ["oxygen", "Oxygen"],
            ["backup_oxygen", "Backup"],
            ["parts", "Spare parts"],
            ["repair_turns_remaining", "Repair turns"],
          ].map(([key, title]) => {
            const value = state[key],
              delta =
                typeof before[key] === "number" && typeof value === "number"
                  ? value - before[key]
                  : 0;
            return (
              <div className="metric" key={key}>
                <span>{title}</span>
                <strong data-testid={`metric-${key}`}>{value ?? "—"}</strong>
                {delta !== 0 && (
                  <b className={delta < 0 ? "down" : "up"}>
                    {delta > 0 ? "+" : ""}
                    {delta}
                  </b>
                )}
              </div>
            );
          })}
          <div className="world-flags">
            <span className={state.leak_active ? "warning" : "healthy"}>
              {state.leak_active ? "Leak active" : "No active leak"}
            </span>
            <span className={state.crew_alive ? "healthy" : "warning"}>
              {state.crew_alive ? "Crew alive" : "Crew lost"}
            </span>
          </div>
          <small>
            Audit perspective · crew AIs receive public projections only.
          </small>
        </div>
      </div>
      <div className="transport">
        <Button
          variant="ghost"
          aria-label="Restart run"
          title="Restart run"
          onClick={() => {
            setPlaying(false);
            setCursor(0);
          }}
        >
          <RotateCcw size={16} />
        </Button>
        <Button
          variant="ghost"
          aria-label="Previous step"
          disabled={cursor === 0}
          onClick={() => {
            setPlaying(false);
            setCursor(cursor - 1);
          }}
        >
          <SkipBack size={17} />
        </Button>
        <Button
          variant="ghost"
          className="play-button"
          aria-label={playing ? "Pause run" : "Play run"}
          onClick={() => {
            if (cursor === events.length - 1) setCursor(0);
            setPlaying(!playing);
          }}
        >
          {playing ? <Pause size={18} /> : <Play size={18} />}
        </Button>
        <Button
          variant="ghost"
          aria-label="Next step"
          disabled={cursor === events.length - 1}
          onClick={() => {
            setPlaying(false);
            setCursor(cursor + 1);
          }}
        >
          <SkipForward size={17} />
        </Button>
        <input
          aria-label="Run progress"
          type="range"
          min={0}
          max={events.length - 1}
          value={cursor}
          onChange={(event) => {
            setPlaying(false);
            setCursor(Number(event.target.value));
          }}
        />
        <span>
          {cursor + 1} / {events.length}
        </span>
        <select
          aria-label="Playback speed"
          value={speed}
          onChange={(event) => setSpeed(Number(event.target.value))}
        >
          <option value={0.5}>0.5×</option>
          <option value={1}>1×</option>
          <option value={2}>2×</option>
          <option value={4}>4×</option>
        </select>
        <span className="provenance">{run.provenance}</span>
      </div>
    </section>
  );
}
