import { useDiagramLayout } from "./diagram/useDiagramLayout";
import { routePath, CARD_WIDTH, CARD_HEIGHT } from "./diagram/layout";
import type { DiagramRoute, DiagramPort } from "./diagram/layout";
import { memo, useEffect, useMemo, useRef } from "react";
import {
  Background,
  BaseEdge,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useReactFlow,
  useViewport,
  useUpdateNodeInternals,
} from "@xyflow/react";
import type { Edge, EdgeProps, Node, NodeProps } from "@xyflow/react";
import {
  ArrowDownLeft,
  Bot,
  CalendarDays,
  ChartNoAxesColumnIncreasing,
  CheckCheck,
  Clock3,
  Cloud,
  Code2,
  Database,
  Gauge,
  Layers3,
  ListChecks,
  Maximize,
  Network,
  Repeat2,
  Route,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  UserRound,
  Workflow,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useProject, decisionStyle } from "./core/project";
import type {
  Component,
  Connection,
  Mode,
  DecisionHighlight,
} from "./core/project";

export const icons = {
  user: UserRound,
  calendar: CalendarDays,
  search: Search,
  shield: ShieldCheck,
  gear: SlidersHorizontal,
  repeat: Repeat2,
  layers: Layers3,
  route: Route,
  clock: Clock3,
  check: CheckCheck,
  bot: Bot,
  network: Network,
  list: ListChecks,
  gauge: Gauge,
  database: Database,
  chart: ChartNoAxesColumnIncreasing,
  cloud: Cloud,
};
interface CardData extends Record<string, unknown> {
  ports: DiagramPort[];
  decision?: DecisionHighlight | null;
  component: Component;
  tone: string;
  active: boolean;
  dim: boolean;
  onCode: (id: string) => void;
}
interface BoundaryData extends Record<string, unknown> {
  title: string;
  subtitle: string;
  color: string;
  external: boolean;
}
interface WireData extends Record<string, unknown> {
  route: DiagramRoute;
  message: Connection;
  active: boolean;
  dim: boolean;
  inspect: (id: string) => void;
  labels: boolean;
}

const ComponentNode = memo(({ data, id }: NodeProps<Node<CardData>>) => {
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => {
    updateNodeInternals(id);
  }, [id, data.ports, updateNodeInternals]);
  const Icon = icons[data.component.icon as keyof typeof icons] ?? Workflow;
  return (
    <div
      data-decision={data.decision?.tone}
      className={`component-card ${data.active ? "is-active" : ""} ${data.dim ? "is-dim" : ""}`}
      style={
        {
          "--tone": data.component.color ?? data.tone,
          ...decisionStyle(data.decision),
        } as React.CSSProperties
      }
    >
      {data.ports.map((port) => (
        <Handle
          key={port.id}
          id={port.id}
          type={port.type}
          position={port.side as Position}
          style={{
            left: port.x,
            top: port.y,
            right: "auto",
            bottom: "auto",
            transform: "translate(-50%, -50%)",
          }}
        />
      ))}
      {data.decision && (
        <span className="canvas-decision-badge">{data.decision.label}</span>
      )}
      <div className="card-heading">
        <span className="component-icon">
          <Icon size={21} />
        </span>
        <strong>{data.component.title}</strong>
        <button
          className="card-code nodrag"
          aria-label={`Open code for ${data.component.title}`}
          onClick={(event) => {
            event.stopPropagation();
            data.onCode(data.component.id);
          }}
        >
          <Code2 size={15} />
        </button>
      </div>
      <p>{data.component.summary}</p>
      <div className="card-foot">
        <code>{data.component.sources[0]?.symbol ?? "Source not indexed"}</code>
        <ArrowDownLeft size={12} />
      </div>
    </div>
  );
});
const BoundaryNode = memo(({ data }: NodeProps<Node<BoundaryData>>) => (
  <div
    className={`boundary ${data.external ? "external" : ""}`}
    style={{ "--tone": data.color } as React.CSSProperties}
  >
    <div className="boundary-heading">
      <span className="boundary-dot" />
      <strong>{data.title}</strong>
    </div>
    <span>{data.subtitle}</span>
  </div>
));
const MessageEdge = memo((props: EdgeProps<Edge<WireData>>) => {
  const { data } = props;
  const { colors, model } = useProject();
  const path = routePath(data!.route.points);
  const { x, y } = data!.route.label;
  return (
    <>
      <BaseEdge
        id={props.id}
        path={path}
        markerEnd={props.markerEnd}
        style={{
          stroke: data?.active ? colors[data!.message.kind] : "#a8aaa4",
          strokeWidth: data?.active ? 2.5 : 1.2,
          opacity: data?.dim ? 0.1 : 0.65,
          strokeDasharray: model.messageKinds[data!.message.kind].private
            ? "7 5"
            : undefined,
        }}
        interactionWidth={20}
      />
      {data?.active && (
        <circle
          className="message-packet"
          r="4"
          fill={colors[data.message.kind]}
        >
          <animateMotion dur="1.8s" repeatCount="indefinite" path={path} />
        </circle>
      )}
      {(data?.labels || data?.active) && (
        <EdgeLabelRenderer>
          <button
            className={`wire-label nodrag nopan ${data.active ? "is-active" : ""}`}
            style={{
              transform: `translate(-50%, -50%) translate(${x}px,${y}px)`,
              opacity: data.dim ? 0.16 : 1,
              color: colors[data.message.kind],
            }}
            onClick={() => data.inspect(props.id)}
            aria-label={`Inspect message ${data.message.label}`}
          >
            {data.message.label}
          </button>
        </EdgeLabelRenderer>
      )}
    </>
  );
});
const nodeTypes = { component: ComponentNode, boundary: BoundaryNode };
const edgeTypes = { message: MessageEdge };

interface Props {
  decision: DecisionHighlight | null;
  view: string;
  showDetails: boolean;
  mode: Mode;
  privateFlows: boolean;
  labels: boolean;
  selected: { kind: "component" | "message"; id: string } | null;
  activeNodes: string[];
  activeEdges: string[];
  follow: boolean;
  onSelect: (kind: "component" | "message", id: string, code?: boolean) => void;
  onClear: () => void;
}
export function Graph({
  decision,
  view,
  showDetails,
  mode,
  privateFlows,
  labels,
  selected,
  activeNodes,
  activeEdges,
  follow,
  onSelect,
  onClear,
}: Props) {
  const { byId, colors, model, visibleComponents, visibleConnections } =
    useProject();
  const api = useReactFlow();
  const container = useRef<HTMLDivElement>(null);
  const { zoom } = useViewport();
  const { components, services, messages } = useMemo(() => {
    const revealed = new Set(follow ? activeNodes : []);
    if (follow)
      model.connections
        .filter((e) => activeEdges.includes(e.id))
        .forEach((e) => {
          revealed.add(e.source);
          revealed.add(e.target);
        });
    if (selected?.kind === "component") revealed.add(selected.id);
    if (selected?.kind === "message")
      model.connections
        .filter((e) => e.id === selected.id)
        .forEach((e) => {
          revealed.add(e.source);
          revealed.add(e.target);
        });
    const base = new Set(
      visibleComponents(view, mode)
        .filter((c) => showDetails || !c.detail)
        .map((c) => c.id),
    );
    const components = model.components.filter(
      (c) =>
        (base.has(c.id) || revealed.has(c.id)) &&
        (!c.modes || c.modes.includes(mode)),
    );
    const services = model.services.filter((service) =>
      components.some((node) => node.service === service.id),
    );
    const messages = visibleConnections(
      components.map((node) => node.id),
      mode,
      privateFlows,
    );
    return { components, services, messages };
  }, [
    view,
    mode,
    showDetails,
    follow,
    activeNodes,
    activeEdges,
    selected,
    privateFlows,
    model,
    visibleComponents,
    visibleConnections,
  ]);
  const arranged = useDiagramLayout({
    services: services.map((s) => ({ id: s.id })),
    items: components.map((c) => ({ id: c.id, service: c.service })),
    links: messages.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.label,
    })),
  });
  const layout = arranged?.layout;
  const { nodes, edges } = useMemo(() => {
    if (!layout) return { nodes: [], edges: [] };
    const focusNodes = new Set(activeNodes);
    const focusEdges = new Set(activeEdges);
    if (selected?.kind === "component") {
      focusNodes.add(selected.id);
      if (!activeNodes.length)
        messages
          .filter(
            (edge) =>
              edge.source === selected.id || edge.target === selected.id,
          )
          .forEach((edge) => {
            focusNodes.add(edge.source);
            focusNodes.add(edge.target);
            focusEdges.add(edge.id);
          });
    } else if (selected?.kind === "message") {
      const message = messages.find((edge) => edge.id === selected.id);
      if (message) {
        focusNodes.add(message.source);
        focusNodes.add(message.target);
        focusEdges.add(message.id);
      }
    }
    const focus = focusNodes.size > 0;
    const graphNodes: Node[] = [];
    services.forEach((service) => {
      const members = components.filter((node) => node.service === service.id);
      graphNodes.push({
        id: `boundary-${service.id}`,
        type: "boundary",
        position: {
          x: layout.boundaries[service.id].x,
          y: layout.boundaries[service.id].y,
        },
        data: { ...service, external: service.external ?? false },
        style: {
          width: layout.boundaries[service.id].width,
          height: layout.boundaries[service.id].height,
        },
        selectable: false,
        draggable: false,
        focusable: false,
        zIndex: -1,
      });
      members.forEach((component) => {
        const position = layout.positions[component.id];
        graphNodes.push({
          id: component.id,
          type: "component",
          position,
          data: {
            component,
            ports: layout.ports[component.id],
            tone: service.color,
            active: focusNodes.has(component.id),
            decision: decision?.actor === component.id ? decision : null,
            dim: focus && !focusNodes.has(component.id),
            onCode: (id: string) => onSelect("component", id, true),
          },
          style: { width: CARD_WIDTH, height: CARD_HEIGHT },
          ariaLabel: `Component ${component.title}`,
          draggable: false,
        });
      });
    });
    const graphEdges: Edge[] = messages.map((message) => {
      const route = layout.routes[message.id];
      return {
        id: message.id,
        type: "message",
        source: message.source,
        target: message.target,
        sourceHandle: `out-${message.id}`,
        targetHandle: `in-${message.id}`,
        data: {
          message,
          route,
          active: focusEdges.has(message.id),
          dim: focus && !focusEdges.has(message.id),
          inspect: (id: string) => onSelect("message", id),
          labels,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: focusEdges.has(message.id) ? colors[message.kind] : "#a8aaa4",
          width: 16,
          height: 16,
        },
        animated: activeEdges.includes(message.id),
        ariaLabel: `${message.label}: ${byId[message.source].title} to ${byId[message.target].title}`,
      };
    });
    graphNodes.push({
      id: "routing-extent",
      type: "boundary",
      position: { x: layout.bounds.width, y: layout.bounds.height },
      data: { title: "", subtitle: "", color: "transparent", external: false },
      style: { width: 1, height: 1, opacity: 0, pointerEvents: "none" },
      selectable: false,
      focusable: false,
    });
    return { nodes: graphNodes, edges: graphEdges };
  }, [
    layout,
    components,
    services,
    messages,
    labels,
    selected,
    activeNodes,
    activeEdges,
    onSelect,
    decision,
    model,
    byId,
    colors,
    visibleComponents,
    visibleConnections,
  ]);
  const cameraBounds = useMemo(() => {
    if (!layout) return undefined;
    if (!follow || !activeNodes.length) return layout.bounds;
    const points = activeNodes.flatMap((id) => {
      const p = layout.positions[id];
      return p ? [p, { x: p.x + CARD_WIDTH, y: p.y + CARD_HEIGHT }] : [];
    });
    activeEdges.forEach((id) =>
      points.push(...(layout.routes[id]?.points ?? [])),
    );
    if (!points.length) return layout.bounds;
    const x = Math.min(...points.map((p) => p.x)),
      y = Math.min(...points.map((p) => p.y));
    return {
      x,
      y,
      width: Math.max(...points.map((p) => p.x)) - x,
      height: Math.max(...points.map((p) => p.y)) - y,
    };
  }, [layout, follow, activeNodes, activeEdges]);
  useEffect(() => {
    if (!api.viewportInitialized || !cameraBounds) return;
    const frame = requestAnimationFrame(() => {
      void api.fitBounds(cameraBounds, { padding: 0.15, duration: 250 });
    });
    return () => cancelAnimationFrame(frame);
  }, [cameraBounds, api]);
  useEffect(() => {
    if (!container.current || !api.viewportInitialized || !cameraBounds) return;
    const observer = new ResizeObserver(() => {
      void api.fitBounds(cameraBounds, { padding: 0.15, duration: 0 });
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, [cameraBounds, api]);
  const focusSelection = () => {
    const ids =
      selected?.kind === "component"
        ? [selected.id]
        : selected?.kind === "message"
          ? model.connections
              .filter((edge) => edge.id === selected.id)
              .flatMap((edge) => [edge.source, edge.target])
          : activeNodes;
    api.fitView({
      nodes: ids.map((id) => ({ id })),
      padding: 0.4,
      maxZoom: 1.25,
      duration: 350,
    });
  };
  return (
    <div
      ref={container}
      className="graph"
      aria-label="Interactive architecture diagram"
    >
      {!layout && (
        <div
          className="layout-status"
          role={arranged?.error ? "alert" : "status"}
        >
          {arranged?.error
            ? `Unable to arrange diagram: ${arranged.error}`
            : "Arranging connections…"}
        </div>
      )}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitViewOptions={{ padding: 0.1, maxZoom: 1.05 }}
        minZoom={0.08}
        maxZoom={2.2}
        nodesConnectable={false}
        nodesDraggable={false}
        edgesFocusable
        onNodeClick={(_, node) => {
          if (node.type === "component") onSelect("component", node.id);
        }}
        onEdgeClick={(_, edge) => onSelect("message", edge.id)}
        onPaneClick={onClear}
        deleteKeyCode={null}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={22} size={1} color="#d9d8ce" />
      </ReactFlow>
      <div className="canvas-controls">
        <button
          aria-label="Zoom out"
          title="Zoom out"
          onClick={() => api.zoomOut({ duration: 160 })}
        >
          <ZoomOut size={17} />
        </button>
        <span data-testid="zoom-level">{Math.round(zoom * 100)}%</span>
        <button
          aria-label="Zoom in"
          title="Zoom in"
          onClick={() => api.zoomIn({ duration: 160 })}
        >
          <ZoomIn size={17} />
        </button>
        <i />
        <button
          aria-label="Fit diagram"
          title="Fit diagram"
          onClick={() =>
            api.fitView({ padding: 0.1, duration: 300, maxZoom: 1.05 })
          }
        >
          <Maximize size={17} />
        </button>
        <button
          aria-label="Focus selection"
          title="Focus selection"
          disabled={!selected && !activeNodes.length}
          onClick={focusSelection}
        >
          <Search size={17} />
        </button>
      </div>
      <div className="canvas-hint">
        Drag to pan <span>·</span> Scroll to zoom <span>·</span> Select a card
        or connection
      </div>
    </div>
  );
}
