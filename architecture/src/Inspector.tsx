import { Fields } from "./ContractFields";
import { InputOutput } from "./InputOutput";
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
import { useProject, format } from "./core/project";
import type { DemoRun, RunRecord, SourceRef } from "./core/project";
export type Selection = { kind: "component" | "message"; id: string };
export type InspectorTab = "overview" | "code" | "state" | "event" | "io";
interface Props {
  selected: Selection;
  tab: InspectorTab;
  setTab: (tab: InspectorTab) => void;
  onClose: () => void;
  onSelect: (kind: "component" | "message", id: string) => void;
  run?: DemoRun;
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
  const { project, model, byId, kindLabels, sourceFor, sourceUrl } =
    useProject();
  const node = selected.kind === "component" ? byId[selected.id] : null;
  const edge =
    selected.kind === "message"
      ? model.connections.find((item) => item.id === selected.id)!
      : null;
  const refs =
    node?.sources ??
    (edge?.contract === undefined ? [] : [model.contracts[edge.contract]]);
  const [sourceChoice, setSourceChoice] = useState(0);
  const [example, setExample] = useState(false);
  useEffect(() => {
    setSourceChoice(0);
    setExample(false);
  }, [selected.id]);
  const source = refs.length
    ? sourceFor(refs[Math.min(sourceChoice, refs.length - 1)])
    : undefined;
  const snapshot =
    node && run && project.replay?.snapshot
      ? project.replay.snapshot(node.id, run, cursor)
      : { label: "No recorded state available.", value: null };
  const previous = snapshot.previous ?? {};
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
            {node ? "COMPONENT INSPECTOR" : "CONNECTION INSPECTOR"}
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
              "io",
              ...(replay ? ["event"] : []),
            ] as InspectorTab[]
          ).map((item) => (
            <TabsTrigger value={item} key={item}>
              {item === "code"
                ? "Code"
                : item === "state"
                  ? "State"
                  : item === "io"
                    ? "I/O"
                    : item === "event"
                      ? "Run event"
                      : "Overview"}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value={tab} className="inspector-content min-h-0">
          {tab === "io" && (
            <InputOutput
              componentId={node?.id}
              connection={edge ?? undefined}
              run={run}
              cursor={cursor}
              replay={replay}
              onSelect={onSelect}
            />
          )}
          {tab === "overview" &&
            (node ? (
              <>
                <p className="lead">{node.summary}</p>
                <Button
                  variant="outline"
                  className="wide"
                  onClick={() => setTab("io")}
                >
                  Input → output schemas & examples
                </Button>
                <h3>Responsibilities</h3>
                <ul className="responsibilities">
                  {node.responsibilities.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <h3>Code backing this component</h3>
                <SourceLinks refs={refs} />
                <h3>Connections</h3>
                <div className="message-list">
                  {connections.map((item) => (
                    <Button
                      variant="ghost"
                      key={item.id}
                      onClick={() => onSelect("message", item.id)}
                    >
                      <span
                        className="message-dot"
                        style={{
                          background: model.messageKinds[item.kind].color,
                        }}
                      />
                      <span>
                        <strong>{item.label}</strong>
                        <small>
                          {item.source === node.id
                            ? "Connects to " + byId[item.target].title
                            : "Connected from " + byId[item.source].title}
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
                <h3>When this connection is used</h3>
                <p>{edge!.when}</p>
                <h3>Failure behavior</h3>
                <p>{edge!.failure}</p>
                {edge!.contract !== undefined && (
                  <>
                    <h3>
                      Contract fields <code>{edge!.contract}</code>
                    </h3>
                    <Fields reference={model.contracts[edge!.contract!]} />
                  </>
                )}
                {edge!.example !== undefined && (
                  <Button
                    variant="ghost"
                    className="secondary wide"
                    onClick={() => setExample(!example)}
                    aria-expanded={example}
                  >
                    <Braces size={16} />
                    Example payload
                  </Button>
                )}
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
          {tab === "code" &&
            (source ? (
              <>
                <p className="muted">
                  Actual source bundled from the repository. Select a definition
                  to inspect its implementation.
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
                  language={source.language}
                  title={`${source.symbol} · lines ${source.startLine}–${source.endLine}`}
                  style={
                    {
                      "--source-line-offset": source.startLine - 1,
                    } as CSSProperties
                  }
                />
              </>
            ) : (
              <p>No source definition is indexed for this selection.</p>
            ))}
          {tab === "state" && (
            <>
              <div className="ownership">
                <Layers3 size={19} />
                <p>
                  {node?.state ??
                    "This connection does not define state ownership. Inspect its components for ownership and any supplied state evidence."}
                </p>
              </div>
              {node && (
                <>
                  <h3>
                    {!project.replay?.snapshot || !run
                      ? "Recorded state unavailable"
                      : replay
                        ? `Recorded state · event ${events[cursor]?.sequence}`
                        : run
                          ? "Demo state · initial event"
                          : "Recorded state unavailable"}
                  </h3>
                  <p className="muted">{snapshot!.label}</p>
                  {snapshot.fields !== undefined ? (
                    <div className="state-table">
                      {Object.entries(snapshot.fields ?? {}).map(
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
                    ? "No state schema is supplied for this component."
                    : "See any supplied contract fields in Overview."}
                </p>
              )}
            </>
          )}
          {tab === "event" && (
            <>
              <p className="muted">
                Exact selected recorded event. Consult the project adapter for
                visibility and redaction rules.
              </p>
              <SourceBlock
                code={format(events[cursor]?.raw)}
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
  const { sourceFor, sourceUrl } = useProject();
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
