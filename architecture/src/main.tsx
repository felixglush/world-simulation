import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./ui.css";
import App from "./App";
import { stationProject } from "./projects/station-control";
import { exampleProject, staticExampleProject } from "./projects/example";
const selected = new URLSearchParams(location.search).get("project");
const project =
  selected === "example"
    ? exampleProject
    : selected === "static-example"
      ? staticExampleProject
      : stationProject;

document.title = `${project.document.title} · Architecture explorer`;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App
      project={project}
      initialRunId={
        new URLSearchParams(location.search).get("run") ?? undefined
      }
    />
  </StrictMode>,
);
