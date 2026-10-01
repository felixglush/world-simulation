import { memo, useEffect, useMemo, useRef } from "react";
import {
  Background,
  BaseEdge,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  getSmoothStepPath,
  useReactFlow,
  useViewport,
  useNodesInitialized,
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
  message: Connection;
  active: boolean;
  dim: boolean;
  inspect: (id: string) => void;
  labels: boolean;
}

const ComponentNode = memo(({ data }: NodeProps<Node<CardData>>) => {
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
      {[Position.Left, Position.Right, Position.Top, Position.Bottom].map(
        (position) => (
          <span key={position}>
            <Handle id={`in-${position}`} type="target" position={position} />
            <Handle id={`out-${position}`} type="source" position={position} />
          </span>
        ),
      )}
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
  const [path, x, y] = getSmoothStepPath({
    ...props,
    borderRadius: 14,
    offset: 26,
  });
  return (
    <>
      <BaseEdge
        id={props.id}
        path={path}
        markerEnd={props.markerEnd}
        style={{
          stroke: colors[data!.message.kind],
          strokeWidth: data?.active ? 3 : 1.65,
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
      {data?.labels && (
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
  const initialized = useNodesInitialized();
  const { zoom } = useViewport();
  const { nodes, edges } = useMemo(() => {
    const components = visibleComponents(view, mode);
    const services = model.services.filter((service) =>
      components.some((node) => node.service === service.id),
    );
    const messages = visibleConnections(
      components.map((node) => node.id),
      mode,
      privateFlows,
    );
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
    const positions: Record<string, { x: number; y: number; column: number }> =
      {};
    const graphNodes: Node[] = [];
    services.forEach((service, column) => {
      const members = components.filter((node) => node.service === service.id);
      graphNodes.push({
        id: `boundary-${service.id}`,
        type: "boundary",
        position: { x: column * 324, y: 0 },
        data: { ...service, external: service.external ?? false },
        style: { width: 286, height: members.length * 155 + 88 },
        selectable: false,
        draggable: false,
        focusable: false,
        zIndex: -1,
      });
      members.forEach((component, row) => {
        const position = { x: column * 324 + 18, y: 76 + row * 155 };
        positions[component.id] = { ...position, column };
        graphNodes.push({
          id: component.id,
          type: "component",
          position,
          data: {
            component,
            tone: service.color,
            active: focusNodes.has(component.id),
            decision: decision?.actor === component.id ? decision : null,
            dim: focus && !focusNodes.has(component.id),
            onCode: (id: string) => onSelect("component", id, true),
          },
          style: { width: 250 },
          ariaLabel: `Component ${component.title}`,
          draggable: false,
        });
      });
    });
    const graphEdges: Edge[] = messages.map((message) => {
      const from = positions[message.source],
        to = positions[message.target];
      const forward = to.column > from.column;
      const vertical = to.column === from.column;
      const bypass =
        vertical && (from.y > to.y || Math.abs(from.y - to.y) > 160);
      return {
        id: message.id,
        type: "message",
        source: message.source,
        target: message.target,
        sourceHandle: `out-${vertical ? (bypass ? "right" : "bottom") : forward ? "right" : "left"}`,
        targetHandle: `in-${vertical ? (bypass ? "right" : "top") : forward ? "left" : "right"}`,
        data: {
          message,
          active: focusEdges.has(message.id),
          dim: focus && !focusEdges.has(message.id),
          inspect: (id: string) => onSelect("message", id),
          labels,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: colors[message.kind],
          width: 16,
          height: 16,
        },
        animated: activeEdges.includes(message.id),
        ariaLabel: `${message.label}: ${byId[message.source].title} to ${byId[message.target].title}`,
      };
    });
    return { nodes: graphNodes, edges: graphEdges };
  }, [
    view,
    mode,
    privateFlows,
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
  useEffect(() => {
    if (initialized)
      void api.fitView({ padding: 0.1, duration: 200, maxZoom: 1.05 });
  }, [initialized, view, mode, api]);
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(() => {
      void api.fitView({ padding: 0.1, duration: 0, maxZoom: 1.05 });
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, [api]);
  const activeKey = activeNodes.join(",");
  useEffect(() => {
    if (!initialized || !follow || !activeKey) return;
    void api.fitView({
      nodes: activeKey.split(",").map((id) => ({ id })),
      padding: 0.35,
      maxZoom: 1.15,
      duration: 350,
    });
  }, [activeKey, follow, initialized, api]);
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
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.1, maxZoom: 1.05 }}
        minZoom={0.2}
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
        <Background gap={22} size={1} color="#c8d5df" />
        <MiniMap
          nodeColor={(node) =>
            node.type === "boundary"
              ? "#e8eff3"
              : (byId[node.id]?.color ?? "#70b1c4")
          }
          nodeStrokeWidth={0}
          pannable
          zoomable
          ariaLabel="Diagram minimap"
        />
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
