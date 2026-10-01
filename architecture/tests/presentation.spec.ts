import { test, expect } from "@playwright/test";

test("one quiet canvas separates focus from detail and reveals selected code", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("navigation", { name: "Architecture navigation" }),
  ).toBeHidden();
  await expect(
    page.getByRole("button", { name: "All components", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator('.react-flow__node[data-id="budget"]')).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: "Everything", exact: true }).click();
  await page.getByText("Display", { exact: true }).click();
  await page.getByRole("checkbox", { name: "Implementation details" }).check();
  await expect(
    page.locator('.react-flow__node[data-id="budget"]'),
  ).toBeVisible();
  await page.getByText("Display", { exact: true }).click();
  await page.getByRole("button", { name: "Browse components" }).click();
  await page
    .getByRole("button", { name: "Inspect Jev classifier", exact: true })
    .click();
  await expect(
    page.getByRole("complementary", { name: "Inspector" }),
  ).toBeVisible();
});

test("replay resumes and follows actors outside the selected focus", async ({
  page,
}) => {
  await page.goto("/?run=demo_deception");
  const slider = page.getByRole("slider", { name: "Run progress" });
  const { readFileSync } = await import("node:fs");
  const run = JSON.parse(readFileSync("src/demo-runs.json", "utf8")).find(
    (run: any) => run.id === "demo_deception",
  );
  const events = run.records.filter(
    (record: any) => record.record_type === "event",
  );
  const laterAdversary = events.findIndex(
    (event: any, index: number) =>
      index > 1 && event.event_type === "adversary_decision",
  );
  expect(laterAdversary).toBeGreaterThan(1);
  await slider.fill(String(laterAdversary));
  const position = await slider.inputValue();
  await page
    .getByRole("button", { name: "Crew response", exact: true })
    .click();
  await expect(
    page.locator('.react-flow__node[data-id="adversary"]'),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close run player" }).click();
  await page
    .getByRole("button", { name: "Resume replay", exact: true })
    .click();
  await expect(slider).toHaveValue(position);
});

test("opening the recorded message gives code more room and closing restores the canvas", async ({
  page,
}) => {
  await page.goto("/?run=demo_deception");
  const player = page.getByRole("region", { name: "Turn walkthrough" });
  const collapsed = (await player.boundingBox())!.height;
  await player.getByText("Recorded message", { exact: true }).click();
  const code = player.locator(".event-payload code");
  expect((await code.boundingBox())!.height).toBeGreaterThan(300);
  await expect(
    page.getByRole("button", { name: "Next step", exact: true }),
  ).toBeInViewport();
  await player.getByText("Recorded message", { exact: true }).click();
  expect((await player.boundingBox())!.height).toBe(collapsed);
  await page.setViewportSize({ width: 390, height: 844 });
  await player.getByText("Recorded message", { exact: true }).click();
  expect((await code.boundingBox())!.height).toBeGreaterThan(180);
  await expect(
    page.getByRole("button", { name: "Next step", exact: true }),
  ).toBeInViewport();
});
