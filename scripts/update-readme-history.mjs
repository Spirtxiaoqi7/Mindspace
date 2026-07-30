import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const projectRoot = resolve(import.meta.dirname, "..");
const readmePath = resolve(projectRoot, "README.md");
const historyPath = resolve(projectRoot, "docs", "release-history.json");
const startMarker = "<!-- release-history:start -->";
const endMarker = "<!-- release-history:end -->";

const statusLabels = {
  local_regression_validated: "本地回归通过",
  local_release_validated_online_pending: "本地发布验证通过",
  local_hotfix: "本地修复",
  released: "已发布",
};

function changelogAnchor(item) {
  return `${item.version.replaceAll(".", "")}---${item.published_at}`;
}

function releaseFamily(version) {
  const [major, minor] = String(version).split(".");
  return `${major}.${minor}.x`;
}

function renderHistory(items) {
  const families = new Map();
  for (const item of items) {
    const family = releaseFamily(item.version);
    if (!families.has(family)) families.set(family, []);
    families.get(family).push(item);
  }

  const lines = [
    `> 自动同步自 [docs/release-history.json](docs/release-history.json)，当前共 **${items.length}** 个版本节点。`,
    "",
  ];

  for (const [family, releases] of families) {
    lines.push(`#### ${family}`, "", "| 版本 | 日期 | 主题 | 状态 |", "| --- | --- | --- | --- |");
    for (const item of releases) {
      const status = statusLabels[item.status] || (item.status ? item.status : "已记录");
      lines.push(
        `| [${item.version}](CHANGELOG.md#${changelogAnchor(item)}) | ${item.published_at} | ${item.title} | ${status} |`,
      );
    }
    lines.push("");
  }

  return lines.join("\n").trimEnd();
}

const history = JSON.parse(readFileSync(historyPath, "utf8"));
if (!Array.isArray(history) || history.length === 0) {
  throw new Error("docs/release-history.json must contain a non-empty array");
}

const readme = readFileSync(readmePath, "utf8");
const start = readme.indexOf(startMarker);
const end = readme.indexOf(endMarker);
if (start === -1 || end === -1 || end <= start) {
  throw new Error("README release history markers are missing or out of order");
}

const rendered = renderHistory(history);
const updated = `${readme.slice(0, start + startMarker.length)}\n${rendered}\n${readme.slice(end)}`;
writeFileSync(readmePath, updated, "utf8");
process.stdout.write(`README_RELEASES=${history.length}\n`);
