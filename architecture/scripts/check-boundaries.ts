import { readFileSync, readdirSync } from "node:fs";
import { resolve, dirname, relative } from "node:path";
import { parse } from "@babel/parser";
const root = resolve(import.meta.dirname, "../src");
function visit(dir: string) {
  for (const item of readdirSync(dir, { withFileTypes: true })) {
    const path = resolve(dir, item.name);
    if (item.isDirectory()) {
      if (item.name !== "projects") visit(path);
      continue;
    }
    if (!/\.tsx?$/.test(path) || item.name === "main.tsx") continue;
    const file = parse(readFileSync(path, "utf8"), {
      sourceType: "module",
      plugins: ["typescript", "jsx"],
      createImportExpressions: true,
    });
    function scan(value: unknown) {
      if (!value || typeof value !== "object") return;
      if (Array.isArray(value)) {
        value.forEach(scan);
        return;
      }
      const node = value as {
        type?: string;
        source?: { value?: unknown };
        [key: string]: unknown;
      };
      if (
        [
          "ImportDeclaration",
          "ExportNamedDeclaration",
          "ExportAllDeclaration",
          "ImportExpression",
        ].includes(node.type ?? "") &&
        typeof node.source?.value === "string"
      ) {
        const spec = node.source.value;
        const target = spec.startsWith("@/")
          ? resolve(root, spec.slice(2))
          : resolve(dirname(path), spec);
        if (
          target.startsWith(resolve(root, "projects") + "/") ||
          /\.json(\?|$)/.test(spec)
        )
          throw new Error(
            `${relative(root, path)} imports project data: ${spec}`,
          );
      }
      Object.values(node).forEach(scan);
    }
    scan(file);
  }
}
visit(root);
console.log(
  "Shared UI/core have no project adapter or data imports; main.tsx is the composition root.",
);
