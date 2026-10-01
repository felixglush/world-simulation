import { useEffect, useRef } from "react";
import {
  LockKeyhole,
  PanelLeftClose,
  Search,
  ChevronRight,
  Network,
} from "lucide-react";
import { icons } from "./Graph";
import { useProject } from "./core/project";
import type { Selection } from "./Inspector";
export function ComponentBrowser({
  query,
  setQuery,
  selected,
  onInspect,
  onClose,
}: {
  query: string;
  setQuery: (value: string) => void;
  selected: Selection | null;
  onInspect: (kind: "component" | "message", id: string) => void;
  onClose: () => void;
}) {
  const { model, byId } = useProject();
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => {
    search.current?.focus();
  }, []);
  const matches = model.components.filter((node) =>
    [node.title, node.summary, ...node.sources.map((s) => s.symbol)]
      .join(" ")
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const messageMatches = query
    ? model.connections.filter((edge) =>
        `${edge.label} ${edge.description}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      )
    : [];
  return (
    <nav className="sidebar mobile-open" aria-label="Architecture navigation">
      <div className="sidebar-top">
        <span className="eyebrow">EXPLORE THE SYSTEM</span>
        <button
          className="mobile-browse icon-button"
          aria-label="Close navigation"
          onClick={() => onClose()}
        >
          <PanelLeftClose size={17} />
        </button>
      </div>
      <div className="search-field">
        <Search size={15} />
        <input
          ref={search}
          type="search"
          aria-label="Find a component or message"
          placeholder="Find component, message…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <kbd>/</kbd>
      </div>
      <div className="nav-heading">
        {query ? "SEARCH RESULTS" : "COMPONENTS"} <span>{matches.length}</span>
      </div>
      <div className="component-list">
        {model.services.map((service) => {
          const members = matches.filter((node) => node.service === service.id);
          return members.length ? (
            <div key={service.id}>
              <div className="service-heading">
                <i style={{ background: service.color }} />
                {service.title}
              </div>
              {members.map((node) => {
                const Icon = icons[node.icon as keyof typeof icons] ?? Network;
                return (
                  <button
                    key={node.id}
                    aria-label={`Inspect ${node.title}`}
                    className={selected?.id === node.id ? "active" : ""}
                    onClick={() => onInspect("component", node.id)}
                  >
                    <Icon size={15} />
                    <span>{node.title}</span>
                    {selected?.id === node.id && <ChevronRight size={12} />}
                  </button>
                );
              })}
            </div>
          ) : null;
        })}
        {messageMatches.map((edge) => (
          <button
            className="search-message"
            key={edge.id}
            onClick={() => onInspect("message", edge.id)}
          >
            <span>
              {edge.label}
              <small>
                {byId[edge.source].title} → {byId[edge.target].title}
              </small>
            </span>
          </button>
        ))}
        {!matches.length && !messageMatches.length && (
          <p className="empty">No matching components or messages.</p>
        )}
      </div>
      <div className="sidebar-footer">
        <LockKeyhole size={15} />
        <p>{model.description}</p>
      </div>
    </nav>
  );
}
