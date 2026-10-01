import { test, expect } from "@playwright/test";

test("the same explorer renders a TypeScript job pipeline and its replay", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/?project=example");
  await expect(page.locator(".brand")).toContainText("JOB QUEUE");
  await expect(
    page.locator('.react-flow__node[data-id="worker"]'),
  ).toBeVisible();
  await expect(page.locator('.react-flow__node[data-id="world"]')).toHaveCount(
    0,
  );
  await page
    .getByRole("button", { name: "Inspect Worker", exact: true })
    .click();
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await inspector.getByRole("tab", { name: "Code", exact: true }).click();
  await expect(inspector.locator(".code-block")).toContainText(
    "function processJob",
  );
  await expect(
    inspector.locator('.code-block pre span[style*="color"]').first(),
  ).toBeVisible();
  await inspector.getByRole("tab", { name: "I/O", exact: true }).click();
  await inspector
    .getByRole("region", { name: "Inputs contracts" })
    .locator("summary")
    .click();
  await expect(
    inspector.getByRole("region", { name: "Inputs contracts" }),
  ).toContainText("text");
  await page.getByRole("button", { name: "Walk through a job" }).click();
  await expect(page.getByTestId("metric-completed")).toHaveText("0");
  await page
    .getByRole("button", { name: "Next decision", exact: true })
    .click();
  await expect(page.locator(".decision-spotlight")).toContainText(
    "Worker · Completed",
  );
  await expect(page.getByTestId("metric-completed")).toHaveText("1");
  await expect(inspector.locator(".state-table")).toContainText("completed");
  await inspector.getByRole("tab", { name: "I/O", exact: true }).click();
  const recorded = inspector.getByRole("region", {
    name: "Recorded input and output",
  });
  await recorded
    .locator("summary")
    .filter({ hasText: "Recorded output" })
    .click();
  await expect(recorded.locator("details[open] pre")).toContainText(
    '"characters": 5',
  );
  await expect(page.getByRole("button", { name: "Load JSONL" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Download this run" }),
  ).toHaveCount(0);
  await page.goto("/");
  await expect(page.locator(".brand")).toContainText("STATION CONTROL");
  await expect(page.locator('.react-flow__node[data-id="worker"]')).toHaveCount(
    0,
  );
  expect(errors).toEqual([]);
});

test("static projects work without recorded runs or a runtime adapter", async ({
  page,
}) => {
  await page.goto("/?project=static-example");
  await expect(
    page.getByRole("button", { name: "Run replay", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "No recorded runs" }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Inspect Worker", exact: true })
    .click();
  await page.getByRole("tab", { name: "State", exact: true }).click();
  await expect(
    page.getByRole("complementary", { name: "Inspector" }),
  ).toContainText("No recorded state available");
  await page.getByRole("tab", { name: "Code", exact: true }).click();
  await expect(page.locator(".code-block")).toContainText("processJob");
});
