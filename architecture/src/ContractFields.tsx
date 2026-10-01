import { useProject } from "./core/project";
import type { SourceRef } from "./core/project";
export function Fields({ reference }: { reference: SourceRef }) {
  const { sourceFor } = useProject();
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
