import { SlidersHorizontal } from "lucide-react";
import { useProject } from "./core/project";
interface Props {
  view: string;
  onView: (id: string) => void;
  mode: string;
  onMode: (id: string) => void;
  replay: boolean;
  labels: boolean;
  setLabels: (value: boolean) => void;
  privateFlows: boolean;
  setPrivateFlows: (value: boolean) => void;
  follow: boolean;
  setFollow: (value: boolean) => void;
  showDetails: boolean;
  setShowDetails: (value: boolean) => void;
}
/** Project-neutral focus presets and progressively disclosed display controls. */
export function CanvasToolbar(props: Props) {
  const { model } = useProject();
  return (
    <div className="canvas-toolbar">
      <div className="focus-filters" role="group" aria-label="Canvas focus">
        <span>Focus</span>
        {model.views.map((view) => (
          <button
            key={view.id}
            aria-pressed={props.view === view.id}
            onClick={() => props.onView(view.id)}
          >
            {view.title}
          </button>
        ))}
      </div>
      <details className="display-settings">
        <summary>
          <SlidersHorizontal size={14} />
          Display
        </summary>
        <div className="display-panel">
          <label>
            {model.modeLabel ?? "Configuration"}
            <select
              aria-label={model.modeLabel ?? "Configuration"}
              value={props.mode}
              disabled={props.replay}
              onChange={(e) => props.onMode(e.target.value)}
            >
              {model.modes.map((mode) => (
                <option key={mode.id} value={mode.id}>
                  {mode.title}
                </option>
              ))}
            </select>
          </label>
          {model.components.some((c) => c.detail) && (
            <label>
              <input
                type="checkbox"
                checked={props.showDetails}
                onChange={(e) => props.setShowDetails(e.target.checked)}
              />
              Implementation details
            </label>
          )}
          <label>
            <input
              type="checkbox"
              checked={props.labels}
              onChange={(e) => props.setLabels(e.target.checked)}
            />
            Message labels
          </label>
          <label>
            <input
              type="checkbox"
              checked={props.privateFlows}
              onChange={(e) => props.setPrivateFlows(e.target.checked)}
            />
            Show private flows
          </label>
          {props.replay && (
            <label>
              <input
                type="checkbox"
                checked={props.follow}
                onChange={(e) => props.setFollow(e.target.checked)}
              />
              Follow event
            </label>
          )}
          <div className="display-legend">
            {Object.values(model.messageKinds).map((kind) => (
              <span key={kind.label}>
                <i style={{ background: kind.color }} />
                {kind.label}
              </span>
            ))}
          </div>
        </div>
      </details>
    </div>
  );
}
