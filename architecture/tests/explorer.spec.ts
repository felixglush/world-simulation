import { expect, test } from "@playwright/test";

test("inspect a component and follow its message contract", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Architecture explorer" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Browse components" }).click();
  await page
    .getByRole("button", { name: "Inspect Jev classifier", exact: true })
    .click();
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(
    inspector.getByRole("heading", { name: "Jev classifier", exact: true }),
  ).toBeVisible();
  await inspector.getByRole("button", { name: /DispatchJudgment/ }).click();
  await expect(
    inspector.getByText("diagnosis_supported", { exact: true }),
  ).toBeVisible();
  await inspector.getByRole("button", { name: "Example payload" }).click();
  await expect(inspector.locator("pre")).toContainText("urgency");
  expect(errors).toEqual([]);
});

test("search focuses components across views and handles empty results", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Browse components" }).click();
  await page
    .getByRole("searchbox", { name: "Find a component or message" })
    .fill("RunLogWriter");
  await page
    .getByRole("button", { name: "Inspect Run log", exact: true })
    .click();
  await expect(
    page.getByRole("complementary", { name: "Inspector" }),
  ).toContainText("RunLogWriter");
  await page.getByRole("button", { name: "Browse components" }).click();
  await page.getByRole("searchbox").fill("no-such-component");
  await expect(
    page.getByText("No matching components or messages."),
  ).toBeVisible();
});

test("controller modes and private-flow toggle reflect the runtime", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Display", { exact: true }).click();
  await page
    .getByRole("combobox", { name: "Controller mode" })
    .selectOption("rules");
  await expect(page.locator('.react-flow__node[data-id="jev"]')).toHaveCount(0);
  await expect(
    page.locator('.react-flow__node[data-id="rules"]'),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Controller mode" })
    .selectOption("jev+llm");
  await expect(page.locator('.react-flow__node[data-id="jev"]')).toBeVisible();
  await page.getByRole("button", { name: "Everything", exact: true }).click();
  await expect(
    page.locator('.react-flow__edge[data-id="adversary-proposal"]'),
  ).toHaveCount(1);
  await page.getByRole("checkbox", { name: "Message labels" }).check();
  await page.getByRole("checkbox", { name: "Show private flows" }).uncheck();
  await page.getByText("Display", { exact: true }).click();
  await expect(
    page.locator('.react-flow__edge[data-id="adversary-proposal"]'),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", {
      name: "Inspect message DispatchContext",
      exact: true,
    }),
  ).toBeVisible();
});

test("zoom, fit and guided walkthrough work", async ({ page }) => {
  await page.goto("/");
  const zoom = page.getByTestId("zoom-level");
  const before = await zoom.textContent();
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await expect(zoom).not.toHaveText(before!);
  await page.getByRole("button", { name: "Fit diagram" }).click();
  await page.getByRole("button", { name: "Walk through a turn" }).click();
  await expect(
    page.getByRole("region", { name: "Turn walkthrough" }),
  ).toContainText("Adversary proposal");
  await page.getByRole("button", { name: "Next step" }).click();
  await expect(
    page.getByRole("region", { name: "Turn walkthrough" }),
  ).toContainText("World advances");
});

test("small screens retain usable navigation and inspector", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Browse components" }).click();
  await page
    .getByRole("button", { name: "Inspect Jev classifier", exact: true })
    .click();
  await expect(
    page.getByRole("complementary", { name: "Inspector" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close inspector" }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("component exposes actual source and recorded state through a complete run", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Walk through a turn" }).click();
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await page
    .getByRole("group", { name: "Component World engine", exact: true })
    .click();
  await inspector.getByRole("tab", { name: "State", exact: true }).click();
  await expect(inspector).toContainText("Authoritative world state");
  await expect(inspector.locator(".state-table")).toContainText("oxygen");
  await inspector.getByRole("tab", { name: "Code", exact: true }).click();
  await expect(inspector.locator(".code-block")).toContainText(
    "class StationState",
  );
  await inspector.getByRole("tab", { name: "State", exact: true }).click();
  const progress = page.getByRole("slider", { name: "Run progress" });
  await progress.fill((await progress.getAttribute("max")) as string);
  await expect(page.locator(".run-flags")).toContainText("No active leak");
  await expect(page.getByTestId("metric-parts")).toHaveText("1");
  await expect(inspector.locator(".state-table")).toContainText(
    "repairs_completed",
  );
});

test("imports a real log locally and rejects malformed input without breaking the player", async ({
  page,
}) => {
  const { readFileSync } = await import("node:fs");
  const runs = JSON.parse(readFileSync("src/demo-runs.json", "utf8"));
  await page.goto("/");
  await page.getByRole("button", { name: "Walk through a turn" }).click();
  const file = page.getByLabel("Import run file");
  await file.setInputFiles({
    name: "broken.jsonl",
    mimeType: "application/json",
    buffer: Buffer.from("null"),
  });
  await expect(page.getByRole("alert")).toContainText(
    "Every JSONL record must be an object",
  );
  await file.setInputFiles({
    name: "real-run.jsonl",
    mimeType: "application/json",
    buffer: Buffer.from(
      runs[1].records.map((r: unknown) => JSON.stringify(r)).join("\n"),
    ),
  });
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Run input" })).toHaveValue(
    "import-demo_deception",
  );
  const events = runs[1].records.filter((r: any) => r.record_type === "event");
  const dispatch = events.findIndex((r: any) => r.event_type === "dispatch");
  await page
    .getByRole("slider", { name: "Run progress" })
    .fill(String(dispatch));
  await expect(page.locator(".event-stage h3")).toHaveText(
    "Jev classifies the batch",
  );
  await expect(page.locator(".event-stage")).toContainText("Urgency 75");
  const finalState = runs[1].records.at(-1).debug_snapshots.at(-1);
  await page
    .getByRole("slider", { name: "Run progress" })
    .fill(String(events.length - 1));
  await expect(page.getByTestId("metric-oxygen")).toHaveText(
    String(finalState.oxygen),
  );
});

test("standalone HTML works offline without a development server", async ({
  page,
  context,
}) => {
  const { readFileSync } = await import("node:fs");
  await context.setOffline(true);
  // Managed Chromium blocks file: navigation; load the exact built document offline.
  await page.setContent(readFileSync("dist/index.html", "utf8"));
  await expect(
    page.getByRole("heading", { name: "Architecture explorer" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Browse components" }).click();
  await page
    .getByRole("button", { name: "Inspect Jev classifier", exact: true })
    .click();
  await page.getByRole("tab", { name: "Code", exact: true }).click();
  await expect(page.locator(".code-block")).toContainText("class ");
  await expect(
    page.locator(".code-block pre span[style*='color']").first(),
  ).toBeVisible();
});

test("inspector supports keyboard tabs, highlighted source, and copying the definition", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await page.getByRole("button", { name: "Walk through a turn" }).click();
  const progress = page.getByRole("slider", { name: "Run progress" });
  const before = await progress.inputValue();
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await page
    .getByRole("group", { name: "Component World engine", exact: true })
    .click();
  await inspector.getByRole("tab", { name: "Overview", exact: true }).click();
  await page.keyboard.press("ArrowRight");
  await expect(
    inspector.getByRole("tab", { name: "Code", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(progress).toHaveValue(before);
  await expect(
    inspector.locator(".code-block pre span[style*='color']").first(),
  ).toBeVisible();
  await inspector.getByRole("button", { name: "Copy source code" }).click();
  await expect(inspector.getByRole("status")).toHaveText("Copied");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(
    "class StationState",
  );
});

test("decision review highlights actors, distinguishes waits, and pauses at assessments", async ({
  page,
}) => {
  const { readFileSync } = await import("node:fs");
  const run = JSON.parse(readFileSync("src/demo-runs.json", "utf8"))[0];
  const events = run.records.filter((r: any) => r.record_type === "event");
  await page.goto("/");
  await page.getByRole("button", { name: "Walk through a turn" }).click();
  await expect(page.locator(".decision-spotlight")).toContainText(
    "Adversary · Malicious action",
  );
  await expect(
    page.locator(
      '.react-flow__node[data-id="adversary"] .canvas-decision-badge',
    ),
  ).toContainText("Malicious action");
  await page
    .getByRole("button", { name: "Next decision", exact: true })
    .click();
  await expect(page.locator(".decision-spotlight")).toContainText(
    "Jev · Assessment",
  );
  await expect(
    page.locator('.react-flow__node[data-id="jev"] .canvas-decision-badge'),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Next decision", exact: true })
    .click();
  await expect(page.locator(".decision-spotlight")).toContainText(
    "Captain · Decision",
  );
  const waitIndex = events.findIndex(
    (r: any) =>
      r.event_type === "adversary_decision" &&
      r.decision.action.kind === "wait",
  );
  await page
    .getByRole("slider", { name: "Run progress" })
    .fill(String(waitIndex));
  await expect(page.locator(".decision-spotlight")).toContainText(
    "Benign wait",
  );
  await page
    .getByRole("button", { name: "Event journal", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Event filter" })
    .selectOption("decisions");
  await expect(
    page.locator(".event-list button:not([data-decision])"),
  ).toHaveCount(0);
  const assessmentIndex = events.findIndex(
    (r: any) => r.event_type === "dispatch",
  );
  await page
    .getByRole("slider", { name: "Run progress" })
    .fill(String(assessmentIndex - 1));
  await page.getByRole("checkbox", { name: "Pause at decisions" }).check();
  await page.getByRole("button", { name: "Play run", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Play run", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("slider", { name: "Run progress" })).toHaveValue(
    String(assessmentIndex),
  );
});

test("rejected adversary actions stay distinct from benign and unknown choices", async ({
  page,
}) => {
  const { readFileSync } = await import("node:fs");
  const run = JSON.parse(readFileSync("src/demo-runs.json", "utf8"))[0];
  const adversary = run.records.find(
    (r: any) => r.event_type === "adversary_decision",
  );
  adversary.consequence.accepted = false;
  await page.goto("/");
  await page.getByRole("button", { name: "Walk through a turn" }).click();
  const load = async () => {
    await page.getByLabel("Import run file").setInputFiles({
      name: "review.jsonl",
      mimeType: "application/json",
      buffer: Buffer.from(
        run.records.map((r: unknown) => JSON.stringify(r)).join("\n"),
      ),
    });
    await page
      .getByRole("button", { name: "Next decision", exact: true })
      .click();
  };
  await load();
  await expect(page.locator(".decision-spotlight")).toContainText(
    "Malicious action",
  );
  await expect(page.locator(".decision-spotlight")).toContainText("rejected");
  adversary.decision.action.kind = "future_action";
  await load();
  await expect(page.locator(".decision-spotlight")).toContainText(
    "Unclassified action",
  );
});

test("component I/O shows directional schemas, examples, and cursor-bounded recorded pairs", async ({
  page,
}) => {
  const { readFileSync } = await import("node:fs");
  const run = JSON.parse(readFileSync("src/demo-runs.json", "utf8"))[0];
  const events = run.records.filter((r: any) => r.record_type === "event");
  await page.goto("/");
  await page.getByRole("button", { name: "Browse components" }).click();
  await page
    .getByRole("button", { name: "Inspect Jev classifier", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Input → output schemas & examples" })
    .click();
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  const inputs = inspector.getByRole("region", { name: "Inputs contracts" });
  const outputs = inspector.getByRole("region", { name: "Outputs contracts" });
  await inputs
    .locator("summary")
    .filter({ hasText: "DispatchContext" })
    .click();
  await expect(inputs).toContainText("question_version");
  await expect(inputs.locator("details[open] pre")).toContainText('"station"');
  await outputs
    .locator("summary")
    .filter({ hasText: "DispatchJudgment" })
    .click();
  await expect(outputs).toContainText("diagnosis_supported");
  await expect(outputs.locator("details[open] pre")).toContainText(
    '"urgency": 75',
  );
  await page.getByRole("button", { name: "Walk through a turn" }).click();
  await page.getByRole("button", { name: "Browse components" }).click();
  await page
    .getByRole("button", { name: "Inspect Jev classifier", exact: true })
    .click();
  await inspector.getByRole("tab", { name: "I/O", exact: true }).click();
  await expect(inspector).toContainText("No recorded input/output yet");
  const dispatch = events.findIndex((r: any) => r.event_type === "dispatch");
  await page
    .getByRole("slider", { name: "Run progress" })
    .fill(String(dispatch));
  const recorded = inspector.getByRole("region", {
    name: "Recorded input and output",
  });
  await recorded
    .locator("summary")
    .filter({ hasText: "Recorded input" })
    .click();
  await expect(recorded.locator("details[open] pre")).toContainText(
    "station_observation",
  );
  await recorded
    .locator("summary")
    .filter({ hasText: "Recorded output" })
    .click();
  await expect(recorded).toContainText("scripted-demo-jev");
  await page.getByRole("slider", { name: "Run progress" }).fill("0");
  await expect(recorded).toContainText("No recorded input/output yet");
  await expect(recorded.locator("pre")).toHaveCount(0);
});
