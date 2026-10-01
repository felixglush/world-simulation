import { test, expect } from "@playwright/test";

test("a shared example link opens a paused, navigable replay", async ({
  page,
}) => {
  await page.goto("/?run=demo_deception");
  await expect(
    page.getByRole("region", { name: "Turn walkthrough" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Play run", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Next decision", exact: true })
    .click();
  await expect(page.locator(".decision-spotlight")).toBeVisible();
});

test("an unknown run link falls back to the architecture overview", async ({
  page,
}) => {
  await page.goto("/?run=missing");
  await expect(
    page.getByRole("button", { name: "Walk through a turn" }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Turn walkthrough" }),
  ).toHaveCount(0);
});
