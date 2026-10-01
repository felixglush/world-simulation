import { useEffect, useRef, useState } from "react";
import { layoutDiagram } from "./layout";
import type { DiagramLayout, LayoutInput } from "./layout";
/** Cache by topology, not selection color or replay position; ignore stale promises. */
export function useDiagramLayout(input: LayoutInput) {
  const key = JSON.stringify(input);
  const cache = useRef(new Map<string, Promise<DiagramLayout>>());
  const [result, setResult] = useState<{
    key: string;
    layout?: DiagramLayout;
    error?: string;
  }>();
  useEffect(() => {
    let current = true;
    let pending = cache.current.get(key);
    if (!pending) {
      const input: LayoutInput = JSON.parse(key);
      pending = layoutDiagram(input.services, input.items, input.links);
      if (cache.current.size >= 16)
        cache.current.delete(cache.current.keys().next().value!);
      cache.current.set(key, pending);
    }
    pending.then(
      (layout) => {
        if (current) setResult({ key, layout });
      },
      (error) => {
        cache.current.delete(key);
        if (current)
          setResult({
            key,
            error: error instanceof Error ? error.message : "Layout failed",
          });
      },
    );
    return () => {
      current = false;
    };
  }, [key]);
  return result?.key === key ? result : undefined;
}
