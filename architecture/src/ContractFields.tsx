import { sourceFor } from "./model";
import type { SourceRef } from "./model";
export function Fields({ reference }: { reference: SourceRef }) {
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
