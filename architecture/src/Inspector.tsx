import type { CSSProperties } from "react";
import { Button } from "./components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./components/ui/tabs";
import { SourceBlock } from "./SourceBlock";
import { useEffect, useState } from "react";
import {
  ArrowRight,
  Braces,
  Code2,
  ExternalLink,
  Layers3,
  X,
} from "lucide-react";
import {
  byId,
  componentSnapshot,
  format,
  kindLabels,
  model,
  sourceFor,
  sourceUrl,
  worldAt,
} from "./model";
import type { DemoRun, RunRecord, SourceRef } from "./model";
export type Selection = { kind: "component" | "message"; id: string };
export type InspectorTab = "overview" | "code" | "state" | "event";
interface Props {
  selected: Selection;
  tab: InspectorTab;
  setTab: (tab: InspectorTab) => void;
  onClose: () => void;
  onSelect: (kind: "component" | "message", id: string) => void;
  run: DemoRun;
  events: RunRecord[];
  cursor: number;
  replay: boolean;
}
export function Inspector({
  selected,
  tab,
  setTab,
  onClose,
  onSelect,
  run,
  events,
  cursor,
  replay,
}: Props) {
  const node = selected.kind === "component" ? byId[selected.id] : null;
  const edge =
    selected.kind === "message"
      ? model.connections.find((item) => item.id === selected.id)!
      : null;
  const refs = node?.sources ?? [model.contracts[edge!.contract]];
  const [sourceChoice, setSourceChoice] = useState(0);
  const [example, setExample] = useState(false);
  useEffect(() => {
    setSourceChoice(0);
    setExample(false);
  }, [selected.id]);
  const source = sourceFor(refs[Math.min(sourceChoice, refs.length - 1)]);
  const snapshot = node
    ? componentSnapshot(node.id, run, events, cursor)
    : null;
  const previous =
    node && ["world", "adversary-validation"].includes(node.id)
      ? worldAt(events, cursor - 1)
      : {};
  const connections = node
    ? model.connections.filter(
        (item) => item.source === node.id || item.target === node.id,
      )
    : [];
  const stateRefs = (node?.stateTypes ?? []).map(
    (type) => model.contracts[type],
  );
  return (
    <Tabs
      value={tab}
      onValueChange={(value) => setTab(value as InspectorTab)}
      asChild
    >
      <aside className="inspector" aria-label="Inspector">
        <div className="inspector-top">
          <span className="eyebrow">
            {node ? "COMPONENT INSPECTOR" : "MESSAGE CONTRACT"}
          </span>
          <Button
            variant="ghost"
            aria-label="Close inspector"
            onClick={onClose}
          >
            <X size={19} />
          </Button>
        </div>
        <h2>{node?.title ?? edge!.label}</h2>
        <div className="inspector-subtitle">
          {node
            ? model.services.find((item) => item.id === node.service)?.title
            : kindLabels[edge!.kind]}
        </div>
        <TabsList
          variant="line"
          className="inspector-tabs w-auto mx-5 shrink-0"
          aria-label="Inspector sections"
        >
          {(
            [
              "overview",
              "code",
              "state",
              ...(replay ? ["event"] : []),
            ] as InspectorTab[]
          ).map((item) => (
            <TabsTrigger value={item} key={item}>
              {item === "code"
                ? "Code"
                : item === "state"
                  ? "State"
                  : item === "event"
                    ? "Run event"
                    : "Overview"}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value={tab} className="inspector-content min-h-0">
          {tab === "overview" &&
            (node ? (
              <>
                <p className="lead">{node.summary}</p>
                <h3>Responsibilities</h3>
                <ul className="responsibilities">
                  {node.responsibilities.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <h3>Code backing this component</h3>
                <SourceLinks refs={refs} />
                <h3>Messages</h3>
                <div className="message-list">
                  {connections.map((item) => (
                    <Button
                      variant="ghost"
                      key={item.id}
                      onClick={() => onSelect("message", item.id)}
                    >
                      <span className={`message-dot ${item.kind}`} />
                      <span>
                        <strong>{item.label}</strong>
                        <small>
                          {item.source === node.id
                            ? "Sends to " + byId[item.target].title
                            : "Receives from " + byId[item.source].title}
                        </small>
                      </span>
                      <ArrowRight size={15} />
                    </Button>
                  ))}
                </div>
              </>
            ) : (
              <>
                <div className="route-pair">
                  <Button
                    variant="ghost"
                    onClick={() => onSelect("component", edge!.source)}
                  >
                    {byId[edge!.source].title}
                  </Button>
                  <ArrowRight size={16} />
                  <Button
                    variant="ghost"
                    onClick={() => onSelect("component", edge!.target)}
                  >
                    {byId[edge!.target].title}
                  </Button>
                </div>
                <p className="lead">{edge!.description}</p>
                <h3>When this message moves</h3>
                <p>{edge!.when}</p>
                <h3>Failure behavior</h3>
                <p>{edge!.failure}</p>
                <h3>
                  Contract fields <code>{edge!.contract}</code>
                </h3>
                <Fields reference={model.contracts[edge!.contract]} />
                <Button
                  variant="ghost"
                  className="secondary wide"
                  onClick={() => setExample(!example)}
                  aria-expanded={example}
                >
                  <Braces size={16} />
                  Example payload
                </Button>
                {example && (
                  <>
                    <p className="muted">
                      Illustrative payload · selected fields. Replay shows
                      actual recorded values.
                    </p>
                    <SourceBlock
                      code={format(edge!.example)}
                      language="json"
                      title="Example payload"
                    />
                  </>
                )}
                <SourceLinks refs={refs} />
              </>
            ))}
          {tab === "code" && (
            <>
              <p className="muted">
                Actual Python source bundled from the repository. Select a
                definition to inspect its implementation.
              </p>
              <label className="field-label">
                Definition
                <select
                  value={sourceChoice}
                  onChange={(event) =>
                    setSourceChoice(Number(event.target.value))
                  }
                >
                  {refs.map((ref, index) => (
                    <option key={ref.symbol} value={index}>
                      {ref.symbol}
                    </option>
                  ))}
                </select>
              </label>
              <a
                className="source-file"
                href={sourceUrl(source)}
                target="_blank"
                rel="noreferrer"
              >
                {source.path}
                <ExternalLink size={14} />
              </a>
              <SourceBlock
                className="code-block source-code"
                aria-label={`Source code for ${source.symbol}`}
                code={source.code}
                language="python"
                title={`${source.symbol} · lines ${source.startLine}–${source.endLine}`}
                style={
                  {
                    "--source-line-offset": source.startLine - 1,
                  } as CSSProperties
                }
              />
            </>
          )}
          {tab === "state" && (
            <>
              <div className="ownership">
                <Layers3 size={19} />
                <p>
                  {node?.state ??
                    "This is a request or result value passed across a boundary; the receiving component owns any resulting changes."}
                </p>
              </div>
              {node && (
                <>
                  <h3>
                    {replay
                      ? `Recorded state · event ${events[cursor]?.sequence}`
                      : "Demo state · initial event"}
                  </h3>
                  <p className="muted">{snapshot!.label}</p>
                  {["world", "adversary-validation"].includes(node.id) ? (
                    <div className="state-table">
                      {Object.entries(snapshot!.value ?? {}).map(
                        ([key, value]) => {
                          const changed =
                            cursor > 0 &&
                            format(value) !== format(previous[key]);
                          return (
                            <details
                              key={key}
                              className={changed ? "changed" : ""}
                            >
                              <summary>
                                <code>{key}</code>
                                <span>
                                  {typeof value === "object"
                                    ? Array.isArray(value)
                                      ? `${value.length} items`
                                      : "object"
                                    : String(value)}
                                </span>
                                {changed && <b>changed</b>}
                              </summary>
                              {changed && (
                                <div className="state-before">
                                  Before<pre>{format(previous[key])}</pre>
                                </div>
                              )}
                              <pre>{format(value)}</pre>
                            </details>
                          );
                        },
                      )}
                    </div>
                  ) : (
                    <pre>{format(snapshot!.value)}</pre>
                  )}
                </>
              )}
              <h3>Stored state / value types</h3>
              {stateRefs.length ? (
                stateRefs.map((ref) => (
                  <div key={ref.symbol}>
                    <h4>{ref.symbol}</h4>
                    <Fields reference={ref} />
                  </div>
                ))
              ) : (
                <p className="muted">
                  {node
                    ? "This component has no independent domain-state record. Its backing code shows local adapter or file bookkeeping."
                    : "See the message contract fields in Overview."}
                </p>
              )}
            </>
          )}
          {tab === "event" && (
            <>
              <p className="muted">
                Exact selected audit event. Private fields are available for
                review here and are excluded from crew model inputs.
              </p>
              <SourceBlock
                code={format(events[cursor])}
                language="json"
                title="Recorded audit event"
              />
            </>
          )}
        </TabsContent>
      </aside>
    </Tabs>
  );
}
function SourceLinks({ refs }: { refs: SourceRef[] }) {
  return (
    <div className="source-links">
      {refs.map((ref) => (
        <a
          key={`${ref.path}:${ref.symbol}`}
          href={sourceUrl(sourceFor(ref))}
          target="_blank"
          rel="noreferrer"
        >
          <Code2 size={13} />
          <span>
            {ref.symbol}
            <small>{ref.path}</small>
          </span>
          <ExternalLink size={12} />
        </a>
      ))}
    </div>
  );
}
function Fields({ reference }: { reference: SourceRef }) {
  const fields = sourceFor(reference).fields;
  return fields.length ? (
    <div className="contract-fields">
      {fields.map((field) => (
        <div key={field.name}>
          <code>{field.name}</code>
          <span>{field.type}</span>
        </div>
      ))}
    </div>
  ) : (
    <p className="muted">
      See the backing implementation for local fields and behavior.
    </p>
  );
}
