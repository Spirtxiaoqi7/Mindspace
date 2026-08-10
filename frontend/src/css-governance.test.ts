import fs from "node:fs";
import path from "node:path";
import postcss from "postcss";
import { describe, expect, it } from "vitest";

type RuleEntry = {
  file: string;
  selector: string;
  context: string;
  declarations: string;
};

type OverrideManifest = {
  version: number;
  override_groups: Array<{
    key: string;
    reason: "redesign-override-authority" | "base-source-order-contract";
  }>;
};

const SOURCE_ROOT = path.resolve(process.cwd(), "src");
const CSS_FILES = ["styles.css", "redesign.overrides.css"] as const;

function ruleEntries(): RuleEntry[] {
  const entries: RuleEntry[] = [];
  for (const file of CSS_FILES) {
    const root = postcss.parse(fs.readFileSync(path.join(SOURCE_ROOT, file), "utf8"), { from: file });
    root.walkRules((rule) => {
      const ancestors: string[] = [];
      let parent = rule.parent;
      while (parent && parent.type !== "root") {
        if (parent.type === "atrule") ancestors.unshift(`@${parent.name} ${parent.params}`);
        parent = parent.parent;
      }
      if (ancestors.some((item) => /@(?:-\w+-)?keyframes\b/i.test(item))) return;
      const declarations: Array<[string, string, boolean]> = [];
      rule.nodes?.forEach((node) => {
        if (node.type === "decl") declarations.push([node.prop, node.value, Boolean(node.important)]);
      });
      entries.push({
        file,
        selector: rule.selector.trim(),
        context: ancestors.join(" > ") || "root",
        declarations: JSON.stringify(declarations),
      });
    });
  }
  return entries;
}

function grouped(entries: RuleEntry[], keyOf: (entry: RuleEntry) => string) {
  const groups = new Map<string, RuleEntry[]>();
  entries.forEach((entry) => groups.set(keyOf(entry), [...(groups.get(keyOf(entry)) || []), entry]));
  return groups;
}

describe("CSS cascade governance", () => {
  it("keeps the base stylesheet before the named product override authority", () => {
    const main = fs.readFileSync(path.join(SOURCE_ROOT, "main.tsx"), "utf8");
    const base = main.indexOf('import "./styles.css";');
    const overrides = main.indexOf('import "./redesign.overrides.css";');
    expect(base).toBeGreaterThanOrEqual(0);
    expect(overrides).toBeGreaterThan(base);
  });

  it("forbids completely identical selector, at-rule context and declaration blocks", () => {
    const duplicates = [...grouped(ruleEntries(), (entry) => JSON.stringify([
      entry.context,
      entry.selector,
      entry.declarations,
    ])).values()].filter((items) => items.length > 1);
    expect(duplicates, duplicates.map((items) => items.map((item) => `${item.file}: ${item.context} :: ${item.selector}`).join(" | ")).join("\n")).toEqual([]);
  });

  it("allows only reviewed same-selector cascade overrides", () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(SOURCE_ROOT, "css-governance.allowlist.json"), "utf8")) as OverrideManifest;
    const actual = [...grouped(ruleEntries(), (entry) => `${entry.context}\n${entry.selector}`).entries()]
      .filter(([, items]) => new Set(items.map((item) => item.declarations)).size > 1)
      .map(([key]) => key)
      .sort((left, right) => left.localeCompare(right));
    const allowed = manifest.override_groups.map((item) => item.key).sort((left, right) => left.localeCompare(right));
    expect(new Set(allowed).size).toBe(allowed.length);
    expect(actual).toEqual(allowed);
  });
});
