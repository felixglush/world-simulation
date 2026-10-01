import { ArrowRight, ExternalLink } from "lucide-react";
import { Button } from "./components/ui/button";
import { Badge } from "./components/ui/badge";
import { Fields } from "./ContractFields";
import { SourceBlock } from "./SourceBlock";
import { useProject, format } from "./core/project";
import type { Connection, DemoRun } from "./core/project";

export function InputOutput({
  componentId,
  connection,
  run,
  cursor,
  replay,
  onSelect,
}: {
  componentId?: string;
  connection?: Connection;
  run?: DemoRun;
  cursor: number;
  replay: boolean;
  onSelect: (kind: "component" | "message", id: string) => void;
}) {
  const { project, model, byId } = useProject();
  const inputs = model.connections.filter(
    (edge) => edge.target === componentId,
  );
  const outputs = model.connections.filter(
    (edge) => edge.source === componentId,
  );
  const recorded =
    componentId && run
      ? project.replay?.recordedIO(componentId, run, cursor)
      : undefined;
  return (
    <div className="io-inspector">
      <p className="lead">Input → component → output</p>
      <p className="muted">
        Directed message contracts across all configured modes. Each connection
        has its own schema; these are not a single function signature.
      </p>
      {connection ? (
        <>
          <div className="io-route">
            <span>Output from {byId[connection.source].title}</span>
            <ArrowRight size={16} />
            <span>Input to {byId[connection.target].title}</span>
          </div>
          <Contract edge={connection} />
        </>
      ) : (
        <>
          {replay && recorded !== undefined && (
            <section
              aria-label="Recorded input and output"
              className="io-recording"
            >
              <h3>Recorded input → output</h3>
              {recorded ? (
                <>
                  <Badge variant="outline">
                    Event {recorded.sequence} · {project.replay?.labels.tick}{" "}
                    {recorded.tick}
                  </Badge>
                  <p className="muted">
                    Latest record at or before the selected event.{" "}
                    {recorded.note}
                  </p>
                  <details>
                    <summary>Recorded input · evidence/context</summary>
                    <SourceBlock
                      code={format(recorded.input ?? null)}
                      language="json"
                      title="Recorded input (may be partial)"
                    />
                  </details>
                  <details>
                    <summary>Recorded output · decision</summary>
                    <SourceBlock
                      code={format(recorded.output ?? null)}
                      language="json"
                      title="Recorded decision"
                    />
                  </details>
                </>
              ) : (
                <p>No recorded decision yet at this replay point.</p>
              )}
            </section>
          )}
          {(
            [
              ["Inputs", inputs],
              ["Outputs", outputs],
            ] as const
          ).map(([title, edges]) => (
            <section key={title} aria-label={`${title} contracts`}>
              <h3>
                {title} <Badge variant="secondary">{edges.length}</Badge>
              </h3>
              {!edges.length && (
                <p className="muted">
                  No {title.toLowerCase()} are mapped for this component in the
                  current diagram.
                </p>
              )}
              {edges.map((edge) => (
                <details className="io-contract" key={edge.id}>
                  <summary>
                    <span>{edge.contract}</span>
                    <small>
                      {title === "Inputs"
                        ? `From ${byId[edge.source].title}`
                        : `To ${byId[edge.target].title}`}
                    </small>
                  </summary>
                  <Contract edge={edge} />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onSelect("message", edge.id)}
                  >
                    Inspect connection <ArrowRight size={14} />
                  </Button>
                </details>
              ))}
            </section>
          ))}
        </>
      )}
    </div>
  );
}
function Contract({ edge }: { edge: Connection }) {
  const { model, kindLabels, sourceFor, sourceUrl } = useProject();
  const ref = model.contracts[edge.contract];
  return (
    <div className="io-contract-body">
      <Badge variant="outline">{kindLabels[edge.kind]}</Badge>
      <p>{edge.description}</p>
      <h4>Schema · {edge.contract}</h4>
      <p className="muted">
        Field types extracted from the backing definition.
      </p>
      <Fields reference={ref} />
      <a
        className="source-file"
        href={sourceUrl(sourceFor(ref))}
        target="_blank"
        rel="noreferrer"
      >
        {ref.symbol} definition <ExternalLink size={13} />
      </a>
      <h4>Example</h4>
      <p className="muted">
        Illustrative selected fields, not a complete serialized instance.
      </p>
      <SourceBlock
        code={format(edge.example)}
        language="json"
        title={`${edge.contract} example`}
      />
    </div>
  );
}
