import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { layoutDiagram, CARD_WIDTH, CARD_HEIGHT } from "../src/diagram/layout";

function verify(layout: Awaited<ReturnType<typeof layoutDiagram>>) {
  for (const [id, route] of Object.entries(layout.routes)) {
    for (let i = 1; i < route.points.length; i++) {
      const a = route.points[i - 1],
        b = route.points[i];
      expect(
        Math.abs(a.x - b.x) < 0.001 || Math.abs(a.y - b.y) < 0.001,
        `${id} is orthogonal`,
      ).toBeTruthy();
      for (const [node, position] of Object.entries(layout.positions)) {
        const left = position.x + 0.1,
          right = position.x + CARD_WIDTH - 0.1;
        const top = position.y + 0.1,
          bottom = position.y + CARD_HEIGHT - 0.1;
        const overlaps =
          Math.abs(a.x - b.x) < 0.001
            ? a.x > left &&
              a.x < right &&
              Math.max(a.y, b.y) > top &&
              Math.min(a.y, b.y) < bottom
            : a.y > top &&
              a.y < bottom &&
              Math.max(a.x, b.x) > left &&
              Math.min(a.x, b.x) < right;
        expect(overlaps, `${id} crosses ${node}`).toBeFalsy();
      }
    }
  }
}

test("all project focus presets reserve card-free routes in every controller mode", async () => {
  const model = JSON.parse(readFileSync("src/model.json", "utf8"));
  for (const view of model.views)
    for (const mode of ["rules", "llm", "jev+llm"]) {
      const components = model.components.filter(
        (c: any) =>
          view.nodes.includes(c.id) &&
          (c.id !== "rules" || mode === "rules") &&
          (c.id !== "captain" || mode !== "rules") &&
          (c.id !== "jev" || mode === "jev+llm"),
      );
      const ids = new Set(components.map((c: any) => c.id));
      const links = model.connections.filter(
        (e: any) => ids.has(e.source) && ids.has(e.target),
      );
      const services = model.services.filter((s: any) =>
        components.some((c: any) => c.service === s.id),
      );
      verify(await layoutDiagram(services, components, links));
    }
});

test("uneven columns, reverse links, parallel links and self calls avoid card interiors", async () => {
  const services = [{ id: "one" }, { id: "two" }, { id: "three" }];
  const items = [0, 1, 2, 3, 4, 5, 6].map((id) => ({
    id: String(id),
    service: id < 4 ? "one" : id < 6 ? "two" : "three",
  }));
  const links = items.flatMap((from) =>
    items.map((to) => ({
      id: `${from.id}-${to.id}`,
      source: from.id,
      target: to.id,
    })),
  );
  links.push({ id: "parallel", source: "0", target: "6" });
  const layout = await layoutDiagram(services, items, links);
  verify(layout);
  expect(await layoutDiagram(services, items, links)).toEqual(layout);
});

test("rendered arrows avoid component cards with details and replay enabled", async ({
  page,
}) => {
  await page.goto("/?run=demo_deception");
  await page.getByText("Display", { exact: true }).click();
  await page.getByRole("checkbox", { name: "Implementation details" }).check();
  await page.getByRole("checkbox", { name: "Follow event" }).uncheck();
  await page.getByText("Display", { exact: true }).click();
  await page.getByRole("button", { name: "Fit diagram", exact: true }).click();
  await expect(page.locator('.react-flow__node[data-id="api"]')).toBeVisible();
  const collisions = await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll(".component-card")).map(
      (card) => card.getBoundingClientRect(),
    );
    const failures: string[] = [];
    document
      .querySelectorAll<SVGPathElement>(".react-flow__edge-path")
      .forEach((path) => {
        const matrix = path.getScreenCTM();
        if (!matrix) return;
        for (
          let distance = 2;
          distance < path.getTotalLength() - 2;
          distance += 2
        ) {
          const p = path.getPointAtLength(distance).matrixTransform(matrix);
          if (
            cards.some(
              (card) =>
                p.x > card.left + 2 &&
                p.x < card.right - 2 &&
                p.y > card.top + 2 &&
                p.y < card.bottom - 2,
            )
          ) {
            failures.push(path.id);
            break;
          }
        }
      });
    return failures;
  });
  expect(collisions).toEqual([]);
});

test("the asynchronous layout fits all cards on initial load", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".component-card").first()).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => {
        const area = document.querySelector(".graph")!.getBoundingClientRect();
        const cards = Array.from(document.querySelectorAll(".component-card"));
        return (
          cards.length > 0 &&
          cards.every((card) => {
            const box = card.getBoundingClientRect();
            return (
              box.left >= area.left &&
              box.right <= area.right &&
              box.top >= area.top &&
              box.bottom <= area.bottom
            );
          })
        );
      }),
    )
    .toBe(true);
});
