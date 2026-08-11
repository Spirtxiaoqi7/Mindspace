import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const checkOnly = process.argv.includes("--check");
const releaseVersion = JSON.parse(fs.readFileSync(path.join(root, "config/version.json"), "utf8")).product_version;
const overrides = JSON.parse(fs.readFileSync(path.join(root, "config/codebase-index-overrides.json"), "utf8"));
const codebaseIndexName = `CODEBASE_INDEX_${releaseVersion}.md`;
const fileIndexName = `CODEBASE_FILE_INDEX_${releaseVersion}.md`;
const outputPaths = [`docs/${codebaseIndexName}`, `docs/${fileIndexName}`];
const rootFiles = new Set([
  ".dockerignore", ".gitignore", ".gitmodules", "CHANGELOG.md", "Dockerfile", "README.md", "SECURITY.md",
  "THIRD_PARTY_NOTICES.md", "docker-compose.yml", "payload.json", "pyproject.toml", "uv.lock",
]);
const maintainedRoots = [".github", "config", "deploy", "desktop", "docs", "frontend", "scripts", "src", "tests"];
const vendorFiles = ["vendor/cosyvoice_mindspace_worker.py", "vendor/gpt_sovits_mindspace_worker.py"];
const textExtensions = new Set([
  ".cjs", ".conf", ".css", ".example", ".html", ".ini", ".js", ".json", ".md", ".mjs", ".pem", ".ps1",
  ".py", ".sh", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
]);
const excludedDirectoryNames = new Set([
  ".builder-cache", ".git", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__", "bootstrap", "build",
  "coverage", "dist", "node_modules", "reports", "runtime",
]);
const excludedRootPrefixes = [
  ".deploy-", ".pytest-", ".real-api-", ".runtime-", ".test-tmp", ".tmp", ".venv-", ".visual-",
  "artifacts", "assets", "backups", "dist-", "reports", "runtime", "vendor/",
];
const generatedWebHash = /^(?:src\/mindspace_graph\/web|frontend\/dist)\/index-[A-Za-z0-9_-]+\.(?:js|css|map)$/;

const normalize = (value) => value.replaceAll("\\", "/");
const relative = (value) => normalize(path.relative(root, value));
const isRootExcluded = (item) => excludedRootPrefixes.some((prefix) => item === prefix.replace(/\/$/, "") || item.startsWith(prefix));

function includeFile(fullPath) {
  const item = relative(fullPath);
  const segments = item.split("/");
  if (segments.some((segment) => excludedDirectoryNames.has(segment))) return false;
  if (isRootExcluded(item)) return false;
  if (generatedWebHash.test(item)) return false;
  if (item.endsWith(".pyc") || item.endsWith(".map")) return false;
  if (rootFiles.has(item)) return true;
  return textExtensions.has(path.extname(item).toLowerCase());
}

function walk(directory, output) {
  if (!fs.existsSync(directory)) return;
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    const item = relative(full);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) {
      if (excludedDirectoryNames.has(entry.name) || isRootExcluded(item)) continue;
      walk(full, output);
    } else if (entry.isFile() && includeFile(full)) {
      output.add(item);
    }
  }
}

function maintainedFiles() {
  const files = new Set([...rootFiles].filter((item) => fs.existsSync(path.join(root, item))));
  for (const maintainedRoot of maintainedRoots) walk(path.join(root, maintainedRoot), files);
  for (const item of vendorFiles) if (fs.existsSync(path.join(root, item))) files.add(item);
  for (const item of outputPaths) files.add(item); // generated self-reference exception
  return [...files].sort((a, b) => a.localeCompare(b, "en"));
}

function pythonMetadata(files) {
  const pythonFiles = files.filter((item) => item.endsWith(".py") && fs.existsSync(path.join(root, item)));
  if (!pythonFiles.length) return {};
  const candidates = [process.env.PYTHON, path.join(root, ".venv", "Scripts", "python.exe"), "python"].filter(Boolean);
  let lastError = "";
  for (const executable of candidates) {
    if (executable !== "python" && !fs.existsSync(executable)) continue;
    const result = spawnSync(executable, [path.join(root, "scripts/codebase-index-python.py")], {
      cwd: root,
      input: JSON.stringify(pythonFiles),
      encoding: "utf8",
      maxBuffer: 16 * 1024 * 1024,
    });
    if (result.status === 0) return JSON.parse(result.stdout);
    lastError = result.stderr || result.stdout;
  }
  throw new Error(`Python AST metadata extraction failed: ${lastError}`);
}

const firstLine = (value) => value.split(/\r?\n/).map((line) => line.trim()).find(Boolean) ?? "";
const cleanComment = (value) => value.replace(/^[/#*\s!>-]+/, "").replace(/[*\s]+$/, "").trim();
const compact = (items, limit = 8) => [...new Set(items.filter(Boolean))].slice(0, limit).join("; ") || "none";
const escapeCell = (value) => String(value ?? "").replaceAll("|", "\\|").replace(/\r?\n/g, " ").trim() || "none";

function sourceOf(item) {
  const full = path.join(root, item);
  return fs.existsSync(full) ? fs.readFileSync(full, "utf8") : "";
}

function titleOf(source, fallback) {
  return source.match(/^#\s+(.+)$/m)?.[1]?.trim() || fallback;
}

function layerOf(item) {
  if (item.startsWith("src/mindspace_graph/")) return "Core backend";
  if (item.startsWith("frontend/")) return "Web frontend";
  if (item.startsWith("desktop/")) return "Desktop Launcher";
  if (item.startsWith("tests/") || /\.test\.[cm]?[jt]sx?$/.test(item)) return "Tests";
  if (item.startsWith("scripts/")) return "Developer tooling";
  if (item.startsWith("config/") || item.startsWith(".github/")) return "Governance/config";
  if (item.startsWith("docs/") || item.endsWith(".md")) return "Documentation";
  if (item.startsWith("deploy/") || item.startsWith("vendor/")) return "Packaging adapter";
  return "Repository root";
}

function domainOf(item) {
  if (overrides[item]?.domain) return overrides[item].domain;
  const value = item.toLowerCase();
  if (value.includes("destiny")) return "V7 destiny";
  if (value.includes("conversation_run") || value.includes("chat") || value.includes("message")) return "Chat and durable runs";
  if (value.includes("character") || value.includes("profile")) return "Characters and V2 cards";
  if (value.includes("memory") || value.includes("retriev") || value.includes("knowledge") || value.includes("compaction")) return "Memory and retrieval";
  if (value.includes("setting") || value.includes("product_config") || value.includes("secret")) return "Settings and provider";
  if (value.includes("tool") || value.includes("capabilit")) return "Native tools";
  if (value.includes("audio") || value.includes("voice") || value.includes("asr") || value.includes("tts") || value.includes("microphone")) return "Audio and voice";
  if (value.includes("update") || value.includes("release") || value.includes("version") || value.includes("package") || value.includes("runtime")) return "Version and release";
  if (value.includes("api") || value.includes("route")) return "API composition";
  if (value.includes("test") || value.startsWith("tests/")) return "Verification";
  if (value.startsWith("docs/")) return "Documentation governance";
  if (value.startsWith("frontend/")) return "Frontend shell";
  if (value.startsWith("desktop/")) return "Desktop composition";
  if (value.startsWith("scripts/") || value.startsWith("config/") || value.startsWith(".github/")) return "Repository governance";
  return "Core foundation";
}

function statusOf(item, source) {
  if (outputPaths.includes(item) || item === "payload.json" || item === "uv.lock" || item.endsWith("package-lock.json") || item === "docs/release-history.json" || item === "desktop/assets/gpt-sovits-voices.json" || item === "src/mindspace_graph/static/app/index.html") return "generated";
  if (item.includes("run_082_")) return "deprecated";
  const marker = source.match(/^> (?:文档)?状态：([a-z]+)/m)?.[1];
  if (marker) return marker;
  return "current";
}

function boundaryOf(item) {
  if (item.startsWith("src/mindspace_graph/static/app/") || item.startsWith("frontend/")) return "Web public; no Core secrets";
  if (item.startsWith("desktop/")) return item.includes("secret") ? "Launcher public code; OS-encrypted secret boundary" : "Launcher public; no protected Core source";
  if (item.startsWith("src/mindspace_graph/") || item.startsWith("vendor/")) return "Core protected release surface";
  if (item.startsWith("scripts/")) return "Developer tool; release only when allowlisted";
  if (item.startsWith("tests/") || item.startsWith("docs/") || item.startsWith(".github/")) return "Development-only; not runtime payload by default";
  if (item.startsWith("config/")) return item.includes("release") || item.includes("version") || item.includes("ports") ? "Release/config contract; never contains secrets" : "Development/config; never contains secrets";
  if (item === "payload.json" || item === "pyproject.toml" || item === "uv.lock") return "Core release manifest/dependency surface";
  return "Repository governance; inspect allowlist before release";
}

function jsMetadata(source) {
  const exports = [];
  for (const match of source.matchAll(/\bexport\s+(?:default\s+)?(?:async\s+)?(?:class|function|const|let|var|interface|type)\s+([A-Za-z_$][\w$]*)/g)) exports.push(match[1]);
  for (const match of source.matchAll(/module\.exports(?:\.([A-Za-z_$][\w$]*))?\s*=/g)) exports.push(match[1] || "module.exports");
  const ipc = [...source.matchAll(/ipcMain\.(?:handle|on)\(\s*["'`]([^"'`]+)["'`]/g)].map((match) => `IPC ${match[1]}`);
  const imports = [];
  for (const match of source.matchAll(/(?:from\s+|require\(\s*)["'`]([^"'`]+)["'`]/g)) imports.push(match[1]);
  const suites = [...source.matchAll(/(?:describe|it|test)\(\s*["'`]([^"'`]+)["'`]/g)].map((match) => match[1]);
  return { exports: [...exports, ...ipc], imports, suites };
}

function sideEffects(item, source, py) {
  const calls = (py?.calls ?? []).join(" ");
  const combined = `${source}\n${calls}`;
  const effects = [];
  if (/(?:readFile|writeFile|copyFile|mkdir|unlink|Remove-Item|Set-Content|Path\.(?:read|write)|\.open\(|\bopen\()/.test(combined)) effects.push("filesystem");
  if (/(?:sqlite|database|repository|transaction|execute\()/.test(combined)) effects.push("database/state");
  if (/(?:fetch\(|httpx|requests\.|https?\.request|AsyncClient)/.test(combined)) effects.push("network");
  if (/(?:ipcMain|ipcRenderer|contextBridge)/.test(combined)) effects.push("Electron IPC");
  if (/(?:spawn|execFile|subprocess|Start-Process)/.test(combined)) effects.push("process execution");
  if (/(?:process\.env|os\.environ|GetEnvironmentVariable)/.test(combined)) effects.push("environment");
  if (item.endsWith(".css") || item.endsWith(".tsx") || item.endsWith(".html")) effects.push("UI/rendering");
  return compact(effects, 6);
}

function matchingTests(item, allFiles) {
  if (overrides[item]?.tests) return overrides[item].tests;
  if (item.startsWith("tests/") || /\.test\.[cm]?[jt]sx?$/.test(item)) return "direct test file";
  const stem = path.basename(item).replace(/(?:\.test)?\.[^.]+$/, "").replaceAll("-", "_").toLowerCase();
  const candidates = allFiles.filter((candidate) => (candidate.startsWith("tests/") || candidate.includes(".test.")) && candidate.toLowerCase().replaceAll("-", "_").includes(stem));
  return candidates.slice(0, 4).join("; ") || "repository policy / full suite";
}

function describe(item, source, py, js) {
  if (overrides[item]?.responsibility) return overrides[item].responsibility;
  if (item.endsWith(".md")) return `Documents “${titleOf(source, item)}” with ${statusOf(item, source)} authority.`;
  if (item.startsWith("tests/") || /\.test\.[cm]?[jt]sx?$/.test(item)) return `Verifies ${compact(js.suites.length ? js.suites : (py?.exports ?? []).filter((name) => name.startsWith("test_")), 5)}.`;
  if (item.endsWith(".py") && py?.docstring?.length) return py.docstring.join(" ");
  const comment = cleanComment(firstLine(source));
  if (comment && comment.length > 12 && !comment.startsWith("import ") && !comment.startsWith("from ")) return comment;
  const symbols = compact(py?.exports ?? js.exports, 5);
  if (symbols !== "none") return `Defines ${symbols} for the ${domainOf(item)} domain.`;
  if (item.endsWith(".json")) return `Defines governed ${domainOf(item)} data with top-level keys: ${compact(Object.keys(JSON.parse(source || "{}")), 8)}.`;
  if (item.endsWith(".css")) return `Defines the ${domainOf(item)} visual stylesheet and responsive presentation rules.`;
  return `Maintains ${domainOf(item)} configuration or execution behavior.`;
}

function entryOf(item, source, py, js) {
  if (overrides[item]?.entry) return overrides[item].entry;
  if (item.endsWith(".md")) return titleOf(source, item);
  if (item.endsWith(".json")) {
    try { return compact(Object.keys(JSON.parse(source)), 10); } catch { return "JSON document"; }
  }
  return compact(py?.exports ?? js.exports, 10);
}

function dependenciesOf(item, py, js) {
  if (item.endsWith(".md")) return "linked current/historical documentation";
  return compact(py?.imports ?? js.imports, 8);
}

function metadata(files) {
  const pyMap = pythonMetadata(files);
  return files.map((item) => {
    const source = sourceOf(item);
    const py = pyMap[item];
    const js = /\.(?:[cm]?js|tsx?)$/.test(item) ? jsMetadata(source) : { exports: [], imports: [], suites: [] };
    return {
      path: item,
      layer: layerOf(item),
      domain: domainOf(item),
      responsibility: describe(item, source, py, js),
      entry: entryOf(item, source, py, js),
      dependencies: dependenciesOf(item, py, js),
      effects: sideEffects(item, source, py),
      tests: matchingTests(item, files),
      status: statusOf(item, source),
      boundary: boundaryOf(item),
    };
  });
}

function countBy(records, key) {
  const counts = new Map();
  for (const record of records) counts.set(record[key], (counts.get(record[key]) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "en"));
}

function architectureDocument(records) {
  const domains = countBy(records, "domain");
  const layers = countBy(records, "layer");
  return `# Mindspace ${releaseVersion} Codebase Index\n\n> 文档状态：generated。由 \`scripts/generate-codebase-index.mjs\` 生成；不得手工编辑。\n\n## Coverage\n\n维护文件总数：**${records.length}**。逐文件证据见 [${fileIndexName}](${fileIndexName})。\n\n${layers.map(([name, count]) => `- ${name}: ${count}`).join("\n")}\n\n## Runtime boundaries\n\n\`frontend/\` 和 Core 内嵌 Web 资产属于公开 Web；\`desktop/\` 属于公开 Launcher 与受限 preload/IPC；\`src/mindspace_graph/\` 和两份受管 voice worker 属于 Core 保护面；\`scripts/\`、\`tests/\`、大部分 \`docs/\` 是开发工具，不应随 Core 发布。provider 密钥只能通过桌面安全存储或进程环境进入运行时，索引、报告和发布清单不得包含秘密。\n\n## Layer dependency map\n\n\`\`\`mermaid\nflowchart LR\n  User[\"User\"] --> Web[\"Web frontend\"]\n  Web --> Api[\"FastAPI api_routes\"]\n  Launcher[\"Desktop Launcher\"] -->|\"preload / settings bridge\"| Web\n  Launcher -->|\"service supervision\"| Core[\"Core process\"]\n  Api --> Runs[\"conversation_runs durable state\"]\n  Api --> Destiny[\"V7 destiny 6+6\"]\n  Api --> Characters[\"V2 characters\"]\n  Runs --> Graph[\"LangGraph turn\"]\n  Graph --> Provider[\"provider attempts\"]\n  Graph --> Tools[\"native tools\"]\n  Graph --> Memory[\"recent context / summary / RAG\"]\n  Build[\"version + allowlist tools\"] --> CoreRelease[\"signed Core release\"]\n  Build --> LauncherRelease[\"Launcher package\"]\n\`\`\`\n\n## Main data flows\n\n- Chat: \`frontend/src/chat/useConversation.ts\` -> \`api_routes/chat_runs.py\` -> \`conversation_runs.py\` -> \`service.py\` / \`graph.py\` -> provider/tool attempts -> ordered SSE replay.\n- V7: seed -> 8 archetypes -> first six slots -> second six slots -> twelve selections -> V2 synthesis -> commit. A successful half-batch is retained when the other half fails.\n- Settings: Web -> preload -> \`settings-controller.cjs\` -> Core public settings; provider secrets remain in OS-encrypted storage or process memory.\n- Characters: \`frontend/src/characters/\` -> \`api_routes/characters_cards.py\` -> V2 character store and sessions.\n- Release: \`config/version.json\` + \`core-release-allowlist.json\` -> version sync/policy checks -> dry-run -> signed packaging outside CI.\n\n## Modification navigation\n\n| Change | Start here | Required cross-check |\n|---|---|---|\n| API route | \`src/mindspace_graph/api_routes/\` | \`tests/test_api_route_contract.py\`, frontend API caller |\n| Durable chat/recovery | \`conversation_runs.py\`, \`service.py\` | chat state-machine and frontend recovery tests |\n| Tool/provider | \`native_tools.py\`, provider adapter | capabilities, native-tools, provider-attempt tests |\n| V7 destiny | \`destiny.py\`, \`destiny_routes.py\` | 6+6 and dialogue regression tests |\n| Frontend chat | \`frontend/src/chat/\` | contract, component and full frontend tests |\n| Settings | frontend settings + desktop settings controller | secret and bridge tests |\n| Desktop lifecycle/update | desktop controllers + main/preload | desktop full tests, CJS syntax, Windows dry-run |\n| Release/version | \`config/version.json\`, scripts | version, policy, allowlist and source-map gates |\n\n## Domain counts\n\n${domains.map(([name, count]) => `- ${name}: ${count}`).join("\n")}\n\n## Exclusions\n\nExcluded by design: \`.git\`, \`node_modules\`, virtual environments, runtime/user data, reports, test caches, build/dist directories, binary/media/model assets, desktop bootstrap payloads, third-party vendor trees, and generated Web hash files. The two Mindspace-maintained vendor worker adapters remain included. Generated index documents are explicit self-reference exceptions and must index themselves.\n`;
}

function fileDocument(records) {
  const groups = new Map();
  for (const record of records) {
    if (!groups.has(record.domain)) groups.set(record.domain, []);
    groups.get(record.domain).push(record);
  }
  let output = `# Mindspace ${releaseVersion} Per-file Index\n\n> 文档状态：generated。由 \`scripts/generate-codebase-index.mjs\` 生成；每个维护文件恰好一行。\n\n维护文件总数：**${records.length}**。隐藏的 \`INDEXED\` 标记用于严格 completeness check。\n`;
  for (const [domain, items] of [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0], "en"))) {
    output += `\n## ${domain} (${items.length})\n\n| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |\n|---|---|---|---|---|---|---|---|---|\n`;
    for (const item of items.sort((a, b) => a.path.localeCompare(b.path, "en"))) {
      output += `<!-- INDEXED:${item.path} -->\n| \`${escapeCell(item.path)}\` | ${escapeCell(item.layer)} | ${escapeCell(item.responsibility)} | ${escapeCell(item.entry)} | ${escapeCell(item.dependencies)} | ${escapeCell(item.effects)} | ${escapeCell(item.tests)} | ${escapeCell(item.status)} | ${escapeCell(item.boundary)} |\n`;
    }
  }
  return output;
}

function indexedPaths(document) {
  return [...document.matchAll(/<!-- INDEXED:([^>]+) -->/g)].map((match) => match[1]).sort((a, b) => a.localeCompare(b, "en"));
}

function assertCompleteness(files, fileIndex) {
  const indexed = indexedPaths(fileIndex);
  const duplicates = indexed.filter((item, index) => index > 0 && indexed[index - 1] === item);
  const missing = files.filter((item) => !indexed.includes(item));
  const extra = indexed.filter((item) => !files.includes(item));
  if (duplicates.length || missing.length || extra.length || indexed.length !== files.length) {
    throw new Error(`Codebase index completeness failed:\nduplicates=${duplicates.join(", ")}\nmissing=${missing.join(", ")}\nextra=${extra.join(", ")}`);
  }
}

const files = maintainedFiles();
const records = metadata(files);
const architecture = architectureDocument(records);
const fileIndex = fileDocument(records);
assertCompleteness(files, fileIndex);
const outputs = new Map([[outputPaths[0], architecture], [outputPaths[1], fileIndex]]);

if (checkOnly) {
  const drift = [];
  for (const [item, expected] of outputs) {
    const full = path.join(root, item);
    if (!fs.existsSync(full) || fs.readFileSync(full, "utf8") !== expected) drift.push(item);
  }
  if (drift.length) throw new Error(`Generated codebase index is stale: ${drift.join(", ")}`);
  console.log(`Codebase index complete: ${files.length} maintained files`);
} else {
  for (const [item, content] of outputs) fs.writeFileSync(path.join(root, item), content, "utf8");
  console.log(`Codebase index generated: ${files.length} maintained files`);
}
