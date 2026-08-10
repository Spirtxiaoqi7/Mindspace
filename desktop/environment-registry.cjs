const fs = require("node:fs");
const path = require("node:path");

const ASR_MODULES = ["torch", "torchaudio", "numpy", "funasr", "fastapi", "uvicorn", "websockets", "sounddevice"];

function readJson(file, fallback = null) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return fallback; }
}

function writeJsonAtomic(file, value) {
  const temporary = `${file}.${process.pid}.tmp`;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  fs.renameSync(temporary, file);
}

function uniqueCandidates(candidates) {
  const seen = new Set();
  return candidates.filter((candidate) => {
    if (!candidate?.root || !path.isAbsolute(candidate.root)) return false;
    const root = path.resolve(candidate.root);
    const leaf = path.basename(root).toLowerCase();
    if (!new Set(["asr-cuda", ".venv-asr"]).has(leaf) || root === path.parse(root).root) return false;
    const key = root.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    candidate.root = root;
    return true;
  });
}

function fileStamp(file) {
  try {
    const stat = fs.statSync(file);
    return `${stat.size}:${Math.trunc(stat.mtimeMs)}`;
  } catch { return "missing"; }
}

function candidateStamp(root) {
  return [
    fileStamp(path.join(root, "Scripts", "python.exe")),
    fileStamp(path.join(root, ".mindspace-asr-ready.json")),
    fileStamp(path.join(root, "Lib", "site-packages")),
    fileStamp(path.join(root, "pyvenv.cfg")),
  ].join("|");
}

function createEnvironmentRegistry(options) {
  const paths = options.paths;
  const environment = options.environment || process.env;
  const registryPath = path.join(paths.state, "environment-registry.json");
  const cache = new Map();
  const genericCache = new Map();
  let pathFfmpegRoots;
  let registryCache = { stamp: "", value: null };
  let modelRootCache = { expiresAt: 0, fallback: "", value: "" };

  function log(event, details = {}) {
    if (!options.logFile) return;
    try {
      fs.mkdirSync(path.dirname(options.logFile), { recursive: true });
      fs.appendFileSync(options.logFile, `${JSON.stringify({ at: new Date().toISOString(), event, ...details })}\n`);
    } catch {}
  }

  function storedRecord() {
    const stamp = fileStamp(registryPath);
    if (registryCache.value && registryCache.stamp === stamp) return registryCache.value;
    const value = readJson(registryPath, { schema_version: "1.0.0", environments: {} });
    registryCache = { stamp, value };
    return value;
  }

  function writeRegistry(value) {
    writeJsonAtomic(registryPath, value);
    registryCache = { stamp: fileStamp(registryPath), value };
  }

  function candidates(defaultTarget) {
    const stored = storedRecord()?.environments?.["asr-runtime"];
    const migration = readJson(path.join(paths.state, "storage-migration.json"), {});
    return uniqueCandidates([
      { root: stored?.path, source: "registry", priority: 0 },
      { root: environment.MINDSPACE_ASR_VENV, source: "environment", priority: 1 },
      { root: defaultTarget, source: "managed", priority: 2 },
      { root: migration?.source && path.join(migration.source, "environment", "venvs", "asr-cuda"), source: "migration", priority: 3 },
      { root: options.localAppData && path.join(options.localAppData, "Mindspace", "environment", "venvs", "asr-cuda"), source: "legacy-local", priority: 4 },
      { root: options.userDataRoot && path.join(options.userDataRoot, "app", ".venv-asr"), source: "legacy-launcher", priority: 5 },
      { root: path.join(paths.home, ".venv-asr"), source: "legacy-home", priority: 6 },
      { root: !options.packaged && options.developmentRoot && path.join(options.developmentRoot, ".venv-asr"), source: "development", priority: 7 },
    ]).sort((left, right) => left.priority - right.priority);
  }

  function isWithin(root, target) {
    const base = path.resolve(root);
    const resolved = path.resolve(target);
    return resolved === base || resolved.startsWith(`${base}${path.sep}`);
  }

  function legacyHomes() {
    const migration = readJson(path.join(paths.state, "storage-migration.json"), {});
    return [...new Set([
      migration?.source,
      options.localAppData && path.join(options.localAppData, "Mindspace"),
      options.userDataRoot && path.join(options.userDataRoot, "app"),
    ].filter(Boolean).map((root) => path.resolve(root)))];
  }

  function ffmpegRootsFromPath() {
    if (pathFfmpegRoots) return pathFfmpegRoots;
    const candidates = String(environment.PATH || environment.Path || "").split(path.delimiter)
      .map((root) => root.trim()).filter(Boolean);
    const localAppData = options.localAppData || environment.LOCALAPPDATA;
    const userProfile = environment.USERPROFILE;
    const programData = environment.ProgramData || environment.PROGRAMDATA;
    candidates.push(
      localAppData && path.join(localAppData, "Microsoft", "WinGet", "Links"),
      userProfile && path.join(userProfile, "scoop", "apps", "ffmpeg", "current", "bin"),
      programData && path.join(programData, "chocolatey", "bin"),
    );
    const wingetPackages = localAppData && path.join(localAppData, "Microsoft", "WinGet", "Packages");
    if (wingetPackages && fs.existsSync(wingetPackages)) {
      let packages = [];
      try { packages = fs.readdirSync(wingetPackages, { withFileTypes: true }); } catch {}
      for (const entry of packages) {
        if (!entry.isDirectory() || !entry.name.startsWith("Gyan.FFmpeg_")) continue;
        const packageRoot = path.join(wingetPackages, entry.name);
        let releases = [];
        try { releases = fs.readdirSync(packageRoot, { withFileTypes: true }); } catch {}
        for (const release of releases) {
          if (release.isDirectory() && release.name.toLowerCase().startsWith("ffmpeg-")) {
            candidates.push(path.join(packageRoot, release.name, "bin"));
          }
        }
      }
    }
    pathFfmpegRoots = [...new Set(candidates.filter(Boolean).map((root) => path.resolve(root)))]
      .filter((root) => fs.existsSync(path.join(root, "ffmpeg.exe")) && fs.existsSync(path.join(root, "ffprobe.exe")));
    return pathFfmpegRoots;
  }

  function qwen3TtsSources() {
    const localAppData = options.localAppData || environment.LOCALAPPDATA;
    return [...new Set([
      path.join(paths.home, "experimental", "qwen3-tts"),
      localAppData && path.join(localAppData, "Mindspace", "experimental", "qwen3-tts"),
    ].filter(Boolean).map((root) => path.resolve(root)))].filter((root) => {
      const model = path.join(root, "models", "Qwen3-TTS-12Hz-1.7B-CustomVoice");
      const weight = path.join(model, "model.safetensors");
      const launcher = path.join(path.dirname(root), "vllm-omni", "start-qwen3-tts.sh");
      try {
        return fs.existsSync(path.join(model, "config.json"))
          && fs.existsSync(launcher)
          && fs.statSync(weight).size >= 3 * 1024 ** 3;
      } catch { return false; }
    });
  }

  function componentCandidates(component, defaultTarget) {
    const record = storedRecord()?.environments?.[component.id];
    const explicit = {
      "asr-runtime": environment.MINDSPACE_ASR_VENV,
      "tts-runtime": environment.MINDSPACE_TTS_MARKER_ROOT,
      "gpt-sovits-runtime": environment.MINDSPACE_GPT_SOVITS_VENV,
      "qwen3-vllm-runtime": environment.MINDSPACE_QWEN3_RUNTIME_ROOT,
      "gpt-sovits-ffmpeg": environment.MINDSPACE_FFMPEG && path.dirname(environment.MINDSPACE_FFMPEG),
    }[component.id];
    const relativeHome = isWithin(paths.home, defaultTarget) ? path.relative(paths.home, defaultTarget) : "";
    const relativeModels = isWithin(paths.models, defaultTarget) ? path.relative(paths.models, defaultTarget) : "";
    const roots = [
      { root: record?.path, source: "registry", priority: 0 },
      { root: explicit, source: "environment", priority: 1 },
      { root: defaultTarget, source: "managed", priority: 2 },
      ...legacyHomes().flatMap((home, index) => [
        relativeHome && { root: path.join(home, relativeHome), source: "legacy-home", priority: 10 + index },
        relativeModels && { root: path.join(home, "models", relativeModels), source: "legacy-models", priority: 20 + index },
      ]),
      ...(component.id === "gpt-sovits-ffmpeg" ? ffmpegRootsFromPath().map((root, index) => ({ root, source: "system-path", priority: 30 + index })) : []),
      ...(component.id === "qwen3-vllm-runtime" ? qwen3TtsSources().map((root, index) => ({ root, source: "legacy-qwen3-source", priority: 40 + index })) : []),
    ].filter(Boolean);
    return uniqueCandidatesForComponent(roots);
  }

  function uniqueCandidatesForComponent(candidates_) {
    const seen = new Set();
    return candidates_.filter((candidate) => {
      if (!candidate?.root || !path.isAbsolute(candidate.root)) return false;
      candidate.root = path.resolve(candidate.root);
      if (candidate.root === path.parse(candidate.root).root) return false;
      const key = candidate.root.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).sort((left, right) => left.priority - right.priority);
  }

  function modelRootScore(root) {
    const anchors = [
      ["shibing624", "text2vec-base-chinese", "pytorch_model.bin"],
      ["asr", "paraformer-zh-streaming", "model.pt"],
      ["asr", "Fun-ASR-Nano-2512", "model.pt"],
      ["asr", "fsmn-vad", "model.pt"],
      ["asr", "ct-punc", "model.pt"],
      ["tts", "Fun-CosyVoice3-0.5B-2512", "cosyvoice3.yaml"],
      ["tts", "gpt-sovits", "runtime", "GPT_SoVITS", "pretrained_models", "s1v3.ckpt"],
    ];
    return anchors.reduce((score, parts) => score + (fs.existsSync(path.join(root, ...parts)) ? 1 : 0), 0);
  }

  function resolveModelRoot(defaultRoot) {
    const resolvedFallback = path.resolve(defaultRoot);
    if (modelRootCache.expiresAt > Date.now() && modelRootCache.fallback === resolvedFallback) return modelRootCache.value;
    const record = storedRecord()?.environments?.["model-root"];
    const roots = uniqueCandidatesForComponent([
      { root: record?.path, source: "registry", priority: 0 },
      { root: environment.MINDSPACE_MODEL_ROOT, source: "environment", priority: 1 },
      { root: defaultRoot, source: "managed", priority: 2 },
      ...legacyHomes().map((home, index) => ({ root: path.join(home, "models"), source: "legacy-models", priority: 10 + index })),
    ]);
    const ranked = roots.map((candidate) => ({ ...candidate, score: modelRootScore(candidate.root) }))
      .sort((left, right) => right.score - left.score || left.priority - right.priority);
    const selected = ranked[0];
    if (!selected || selected.score === 0) {
      modelRootCache = { expiresAt: Date.now() + 10_000, fallback: resolvedFallback, value: resolvedFallback };
      return resolvedFallback;
    }
    const registry = storedRecord();
    const previous = registry?.environments?.["model-root"];
    if (selected.source !== "managed" && (!previous?.path || path.resolve(previous.path).toLowerCase() !== selected.root.toLowerCase())) {
      try {
        writeRegistry({
          schema_version: "1.0.0",
          environments: {
            ...(registry.environments || {}),
            "model-root": { path: selected.root, source: selected.source, score: selected.score, verified_at: new Date().toISOString() },
          },
        });
      } catch {}
    }
    modelRootCache = { expiresAt: Date.now() + 10_000, fallback: resolvedFallback, value: selected.root };
    return selected.root;
  }

  function inspectCandidate(candidate) {
    const root = candidate.root;
    const stamp = candidateStamp(root);
    const cached = cache.get(root.toLowerCase());
    if (cached?.stamp === stamp) return cached.report;
    const python = path.join(root, "Scripts", "python.exe");
    const exists = fs.existsSync(root);
    if (!fs.existsSync(python)) {
      const report = {
        root, source: candidate.source, exists, ready: false, partial: exists,
        missing: ["Scripts/python.exe"], probeError: "",
      };
      cache.set(root.toLowerCase(), { stamp, report });
      return report;
    }
    const marker = readJson(path.join(root, ".mindspace-asr-ready.json"), {});
    const sitePackages = path.join(root, "Lib", "site-packages");
    const missing = ASR_MODULES.filter((name) => !fs.existsSync(path.join(sitePackages, name)) && !fs.existsSync(path.join(sitePackages, `${name}.py`)))
      .map((name) => `Python 模块 ${name}`);
    if (marker.ready !== true) missing.push("运行时验证凭证");
    const report = {
      root, source: candidate.source, exists: true, ready: missing.length === 0, partial: true,
      missing: [...new Set(missing)], probeError: "", probe: marker,
    };
    cache.set(root.toLowerCase(), { stamp, report });
    return report;
  }

  function writeReadyMarker(report) {
    if (!report.ready) return;
    const marker = path.join(report.root, ".mindspace-asr-ready.json");
    const current = readJson(marker, {});
    if (current.ready === true && current.environment_id === "asr-cuda" && current.schema_version === "2.0.0") return;
    try {
      writeJsonAtomic(marker, {
        schema_version: "2.0.0",
        environment_id: "asr-cuda",
        environment_version: "0.8.3-cu128",
        ready: true,
        python: "Scripts\\python.exe",
        python_version: report.probe.python || "",
        torch_version: report.probe.torch || "",
        funasr_version: report.probe.funasr || "",
        cuda: report.probe.cuda_build || "",
        verified_at: new Date().toISOString(),
      });
    } catch (error) {
      log("environment.marker_write_failed", { id: "asr", path: report.root, error: String(error.message || error) });
    }
  }

  function remember(report) {
    if (report.source === "managed") return;
    const record = storedRecord();
    const previous = record?.environments?.["asr-runtime"];
    if (previous?.path && path.resolve(previous.path).toLowerCase() === report.root.toLowerCase() && previous?.stamp === candidateStamp(report.root)) return;
    writeRegistry({
      schema_version: "1.0.0",
      environments: {
        ...(record.environments || {}),
        "asr-runtime": {
          path: report.root,
          source: report.source,
          stamp: candidateStamp(report.root),
          verified_at: new Date().toISOString(),
        },
      },
    });
  }

  function rememberComponent(component, report) {
    if (report.source === "managed") return;
    const registry = storedRecord();
    const previous = registry?.environments?.[component.id];
    if (previous?.path && path.resolve(previous.path).toLowerCase() === report.root.toLowerCase()) return;
    try {
      writeRegistry({
        schema_version: "1.0.0",
        environments: {
          ...(registry.environments || {}),
          [component.id]: { path: report.root, source: report.source, verified_at: new Date().toISOString() },
        },
      });
    } catch {}
  }

  function inspectGenericCandidate(component, candidate) {
    if (component.id === "qwen3-vllm-runtime" && candidate.source === "legacy-qwen3-source") {
      return {
        root: candidate.root,
        source: candidate.source,
        ready: false,
        partial: true,
        reusable: true,
        present: ["Qwen3-TTS-12Hz-1.7B-CustomVoice/model.safetensors", "vllm-omni/start-qwen3-tts.sh"],
        missing: ["受管启动凭证（可直接生成，无需下载模型）"],
        probeError: "",
      };
    }
    const present = [];
    const missing = [];
    for (const required of component.required || []) {
      const target = path.join(candidate.root, required);
      if (fs.existsSync(target)) present.push(required); else missing.push(required);
    }
    if (component.minimumWeightBytes) {
      const weight = path.join(candidate.root, "model.pt");
      if (fs.existsSync(weight) && fs.statSync(weight).size < component.minimumWeightBytes) missing.push("model.pt（文件不完整）");
    }
    return {
      root: candidate.root, source: candidate.source, ready: missing.length === 0,
      partial: present.length > 0 && missing.length > 0, present, missing: [...new Set(missing)], probeError: "",
    };
  }

  function inspectGeneric(component, defaultTarget) {
    const cacheKey = `${component.id}:${path.resolve(defaultTarget).toLowerCase()}`;
    const cached = genericCache.get(cacheKey);
    if (cached?.expiresAt > Date.now()) return cached.report;
    const reports = componentCandidates(component, defaultTarget).map((candidate) => inspectGenericCandidate(component, candidate));
    const ready = reports.find((report) => report.ready);
    if (ready) {
      rememberComponent(component, ready);
      const report = {
        path: ready.root, ready: true, partial: false, missing: [], discoveryState: "ready",
        candidateCount: reports.length, selectedSource: ready.source,
        discoveryMessage: ready.source === "managed" ? `${component.name} 已验证` : `已找到并复用现有 ${component.name}`,
      };
      genericCache.set(cacheKey, { expiresAt: Date.now() + 5_000, report });
      return report;
    }
    const managed = reports.find((report) => report.root.toLowerCase() === path.resolve(defaultTarget).toLowerCase());
    const partials = reports.filter((report) => report.partial);
    if (managed?.partial || partials.length) {
      const selected = managed || { root: path.resolve(defaultTarget), missing: partials[0]?.missing || [] };
      const reusable = partials.find((report) => report.reusable);
      const report = {
        path: selected.root, ready: false, partial: Boolean(managed?.partial), missing: selected.missing,
        discoveryState: "repairable", candidateCount: reports.length, partialCandidateCount: partials.length,
        selectedSource: managed?.source || "managed",
        discoveryMessage: reusable
          ? "已找到完整的本地 Qwen3 模型与 WSL 启动资源；可直接接入，无需下载"
          : `已找到本地资源但验证未通过；可继续修复 ${selected.missing.slice(0, 2).join("、")}`,
      };
      genericCache.set(cacheKey, { expiresAt: Date.now() + 3_000, report });
      return report;
    }
    const report = {
      path: path.resolve(defaultTarget), ready: false, partial: false, missing: component.required || [],
      discoveryState: "missing", candidateCount: reports.length, partialCandidateCount: 0, selectedSource: "managed",
      discoveryMessage: `未找到可复用的 ${component.name}；可按需下载`,
    };
    genericCache.set(cacheKey, { expiresAt: Date.now() + 3_000, report });
    return report;
  }

  function inspectAsr(defaultTarget) {
    const roots = candidates(defaultTarget);
    const reports = roots.map(inspectCandidate);
    const ready = reports.find((report) => report.ready);
    if (ready) {
      writeReadyMarker(ready);
      remember(ready);
      return {
        path: ready.root, ready: true, partial: false, missing: [],
        discoveryState: "ready", candidateCount: reports.length, selectedSource: ready.source,
        discoveryMessage: ready.source === "managed" ? "ASR CUDA 环境已验证" : "已找到并绑定现有 ASR CUDA 环境",
      };
    }
    const managed = reports.find((report) => report.root.toLowerCase() === path.resolve(defaultTarget).toLowerCase());
    const selected = managed || { root: path.resolve(defaultTarget), exists: false, partial: false, missing: ["ASR CUDA 运行时"] };
    const partialCount = reports.filter((report) => report.partial).length;
    if (selected.partial || partialCount) {
      const bestPartial = selected.partial ? selected : reports.find((report) => report.partial);
      const missing = selected.missing?.length ? selected.missing : bestPartial?.missing || ["运行时验证失败"];
      return {
        path: selected.root, ready: false, partial: Boolean(selected.partial), missing,
        discoveryState: "repairable", candidateCount: reports.length, partialCandidateCount: partialCount,
        selectedSource: selected.source || "managed",
        discoveryMessage: `已发现 ${partialCount} 个环境目录，但没有完整可用项；当前环境缺少 ${missing.slice(0, 2).join("、")}`,
      };
    }
    return {
      path: path.resolve(defaultTarget), ready: false, partial: false, missing: ["ASR CUDA 运行时"],
      discoveryState: "missing", candidateCount: reports.length, partialCandidateCount: 0, selectedSource: "managed",
      discoveryMessage: `未找到可用 ASR 环境；已检查 ${reports.length} 个受信位置`,
    };
  }

  function inspectTarget(component, defaultTarget) {
    if (component?.id === "asr-runtime" || component === "asr-runtime") return inspectAsr(defaultTarget);
    if (!component?.id || !Array.isArray(component.required)) return null;
    return inspectGeneric(component, defaultTarget);
  }

  function resolveTarget(component, defaultTarget) {
    return inspectTarget(component, defaultTarget)?.path || defaultTarget;
  }

  return { inspectTarget, resolveModelRoot, resolveTarget };
}

module.exports = { ASR_MODULES, createEnvironmentRegistry };
