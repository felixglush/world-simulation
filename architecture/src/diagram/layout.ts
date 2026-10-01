import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode, ElkExtendedEdge } from "elkjs/lib/elk-api";
export const CARD_WIDTH = 232;
export const CARD_HEIGHT = 98;
export interface Point {
  x: number;
  y: number;
}
export interface DiagramRoute {
  points: Point[];
  label: Point;
}
export interface DiagramPort extends Point {
  id: string;
  type: "source" | "target";
  side: "left" | "right" | "top" | "bottom";
}
export interface LayoutInput {
  services: { id: string }[];
  items: { id: string; service: string }[];
  links: { id: string; source: string; target: string; label?: string }[];
}
const elk = new ELK();
/** ELK owns grouping, spacing and obstacle-aware routes. React Flow only renders them. */
export async function layoutDiagram(
  services: LayoutInput["services"],
  items: LayoutInput["items"],
  links: LayoutInput["links"],
) {
  const graph = await elk.layout<ElkNode>({
    id: "diagram-root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.spacing.nodeNode": "64",
      "elk.layered.spacing.nodeNodeBetweenLayers": "64",
      "elk.spacing.edgeNode": "32",
      "elk.layered.spacing.edgeNodeBetweenLayers": "32",
      "elk.spacing.edgeEdge": "20",
      "elk.layered.spacing.edgeEdgeBetweenLayers": "20",
      "elk.padding": "[top=28,left=28,bottom=28,right=28]",
      "elk.randomSeed": "1",
      "elk.layered.cycleBreaking.strategy": "MODEL_ORDER",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    },
    children: services.map((service) => ({
      id: `service:${service.id}`,
      layoutOptions: {
        "elk.padding": "[top=76,left=28,bottom=32,right=28]",
        "elk.spacing.nodeNode": "56",
        "elk.layered.spacing.nodeNodeBetweenLayers": "48",
      },
      children: items
        .filter((item) => item.service === service.id)
        .map((item) => ({
          id: `component:${item.id}`,
          width: CARD_WIDTH,
          height: CARD_HEIGHT,
        })),
    })),
    edges: links.map((link) => ({
      id: `connection:${link.id}`,
      sources: [`component:${link.source}`],
      targets: [`component:${link.target}`],
      labels: link.label
        ? [
            {
              text: link.label,
              width: Math.min(210, link.label.length * 5.5 + 14),
              height: 20,
              layoutOptions: { "elk.edgeLabels.placement": "CENTER" },
            },
          ]
        : [],
    })),
  });
  const positions: Record<string, Point> = Object.create(null);
  const boundaries: Record<string, Point & { width: number; height: number }> =
    Object.create(null);
  const offsets: Record<string, Point> = { "diagram-root": { x: 0, y: 0 } };
  const edges: ElkExtendedEdge[] = [];
  function flatten(node: ElkNode, offset: Point) {
    const point = { x: offset.x + (node.x ?? 0), y: offset.y + (node.y ?? 0) };
    offsets[node.id] = point;
    if (node.id.startsWith("service:"))
      boundaries[node.id.slice(8)] = {
        ...point,
        width: node.width!,
        height: node.height!,
      };
    else if (node.id.startsWith("component:"))
      positions[node.id.slice(10)] = point;
    edges.push(...(node.edges ?? []));
    node.children?.forEach((child) => flatten(child, point));
  }
  flatten(graph, { x: 0, y: 0 });
  const routes: Record<string, DiagramRoute> = Object.create(null);
  const ports: Record<string, DiagramPort[]> = Object.fromEntries(
    items.map((item) => [item.id, []]),
  );
  function addPort(
    node: string,
    id: string,
    type: DiagramPort["type"],
    point: Point,
  ) {
    const local = {
      x: point.x - positions[node].x,
      y: point.y - positions[node].y,
    };
    const candidates = [
      { side: "left", d: Math.abs(local.x) },
      { side: "right", d: Math.abs(local.x - CARD_WIDTH) },
      { side: "top", d: Math.abs(local.y) },
      { side: "bottom", d: Math.abs(local.y - CARD_HEIGHT) },
    ] as const;
    const side = [...candidates].sort((a, b) => a.d - b.d)[0].side;
    ports[node].push({ ...local, id, type, side });
  }
  for (const edge of edges) {
    const section = edge.sections?.[0];
    if (!section || edge.sections?.length !== 1)
      throw new Error(`Unsupported route for ${edge.id}`);
    const offset = offsets[edge.container ?? "diagram-root"];
    const points = [
      section.startPoint,
      ...(section.bendPoints ?? []),
      section.endPoint,
    ].map((p) => ({ x: p.x + offset.x, y: p.y + offset.y }));
    const label = edge.labels?.[0];
    let center = points[0],
      longest = 0;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1],
        b = points[i];
      const length = Math.hypot(b.x - a.x, b.y - a.y);
      if (length > longest) {
        longest = length;
        center = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      }
    }
    const id = edge.id.slice(11);
    routes[id] = {
      points,
      label:
        label?.x != null && label.y != null
          ? {
              x: label.x + offset.x + (label.width ?? 0) / 2,
              y: label.y + offset.y + (label.height ?? 0) / 2,
            }
          : center,
    };
    const link = links.find((link) => link.id === id)!;
    addPort(link.source, `out-${id}`, "source", points[0]);
    addPort(link.target, `in-${id}`, "target", points[points.length - 1]);
  }
  return {
    positions,
    boundaries,
    routes,
    ports,
    bounds: { x: 0, y: 0, width: graph.width ?? 0, height: graph.height ?? 0 },
  };
}
export type DiagramLayout = Awaited<ReturnType<typeof layoutDiagram>>;
/** Preserve ELK's exact orthogonal segments, including obstacle clearance. */
export function routePath(points: Point[]) {
  return points.map((p, i) => `${i ? "L" : "M"} ${p.x} ${p.y}`).join(" ");
}
