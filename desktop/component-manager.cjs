const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { GPT_SOVITS_COMPONENTS } = require("./gpt-sovits-catalog.cjs");

const EMBEDDING_FILES = new Set([
  "1_Pooling/config.json",
  "README.md",
  "config.json",
  "modules.json",
  "pytorch_model.bin",
  "sentence_bert_config.json",
  "special_tokens_map.json",
  "tokenizer_config.json",
  "vocab.txt",
]);

const DEFAULT_COMPONENTS = [
  {
    id: "embedding",
    name: "中文向量模型",
    description: "本地 RAG 语义检索",
    provider: "modelscope",
    repo: "Jerry0/text2vec-base-chinese",
    official: { provider: "huggingface", repo: "shibing624/text2vec-base-chinese" },
    target: "assets/models/shibing624/text2vec-base-chinese",
    required: ["config.json", "pytorch_model.bin"],
    estimatedBytes: 409_275_645,
    filter: (file) => EMBEDDING_FILES.has(file.path || file.Path),
  },
  {
    id: "asr",
    name: "Paraformer Streaming",
    description: "中文实时语音识别",
    provider: "modelscope",
    repo: "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
    official: { provider: "huggingface", repo: "funasr/paraformer-zh-streaming" },
    target: "assets/models/asr/paraformer-zh-streaming",
    required: ["config.yaml", "model.pt"],
    minimumWeightBytes: 800_000_000,
    estimatedBytes: 889_360_655,
    optional: true,
    hardware: "nvidia",
  },
  {
    id: "asr-final",
    name: "Fun-ASR Nano 2512",
    description: "中文含糊语句整句复核；缺失时自动回退实时模型",
    provider: "modelscope",
    repo: "FunAudioLLM/Fun-ASR-Nano-2512",
    official: { provider: "huggingface", repo: "FunAudioLLM/Fun-ASR-Nano-2512" },
    target: "assets/models/asr/Fun-ASR-Nano-2512",
    required: ["config.yaml", "model.pt", "Qwen3-0.6B/config.json", "Qwen3-0.6B/tokenizer.json"],
    minimumWeightBytes: 2_000_000_000,
    estimatedBytes: 2_145_456_424,
    optional: true,
    hardware: "nvidia",
  },
  {
    id: "vad",
    name: "FSMN VAD",
    description: "起声、静音与自动断句",
    provider: "modelscope",
    repo: "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    official: { provider: "huggingface", repo: "funasr/fsmn-vad" },
    target: "assets/models/asr/fsmn-vad",
    required: ["config.yaml", "model.pt"],
    minimumWeightBytes: 1_000_000,
    estimatedBytes: 4_033_603,
    optional: true,
    hardware: "nvidia",
  },
  {
    id: "punc",
    name: "CT Punctuation",
    description: "流式识别标点恢复",
    provider: "modelscope",
    repo: "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
    official: { provider: "huggingface", repo: "funasr/ct-punc" },
    target: "assets/models/asr/ct-punc",
    required: ["config.yaml", "model.pt"],
    minimumWeightBytes: 10_000_000,
    estimatedBytes: 296_363_122,
    optional: true,
    hardware: "nvidia",
  },
  {
    id: "asr-runtime",
    name: "ASR CUDA 运行时",
    description: "PyTorch、FunASR 与实时语音服务依赖",
    provider: "installer",
    target: ".venv-asr",
    required: ["Scripts/python.exe", ".mindspace-asr-ready.json"],
    estimatedBytes: 6_300_000_000,
    installScript: "scripts/prepare-asr.ps1",
    installArgs: ["-SkipModels"],
    optional: true,
    hardware: "nvidia",
  },
  {
    id: "tts",
    name: "本地 CosyVoice 3",
    description: "可选的本地克隆音色模型（国内 ModelScope）",
    provider: "modelscope",
    repo: "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
    official: { provider: "huggingface", repo: "FunAudioLLM/Fun-CosyVoice3-0.5B-2512" },
    target: "assets/models/tts/Fun-CosyVoice3-0.5B-2512",
    required: ["cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt"],
    estimatedBytes: 9_747_517_123,
    optional: true,
  },
  {
    id: "tts-runtime",
    name: "CosyVoice 运行时",
    description: "可选；复用 ASR CUDA 环境，只增量安装缺失依赖",
    provider: "installer",
    target: "runtime/components/tts-runtime",
    required: ["ready.json"],
    estimatedBytes: 850_000_000,
    displayEstimatedBytes: false,
    installScript: "scripts/prepare-tts.ps1",
    installArgs: [],
    optional: true,
  },
  ...GPT_SOVITS_COMPONENTS,
];

function encodeRepoPath(value) {
  return String(value).replace(/\\/g, "/").split("/").map(encodeURIComponent).join("/");
}

function normalizeDownloadSource(value) {
  return value === "official" ? "official" : "china";
}

function componentForSource(component, source) {
  if (normalizeDownloadSource(source) !== "official" || !component.official) return component;
  return { ...component, ...component.official };
}

function staticFileForSource(file, source) {
  const selected = normalizeDownloadSource(source);
  return { ...file, url: file.urls?.[selected] || file.url };
}

function reportReady(root, component, resolveTarget) {
  const target = resolveTarget ? resolveTarget(component) : path.join(root, component.target);
  const missing = component.required.filter((file) => !fs.existsSync(path.join(target, file)));
  const present = component.required.filter((file) => fs.existsSync(path.join(target, file)));
  if (component.id === "asr-runtime" && !missing.length) {
    try {
      const marker = JSON.parse(fs.readFileSync(path.join(target, ".mindspace-asr-ready.json"), "utf8"));
      if (marker.ready !== true) missing.push(".mindspace-asr-ready.json（校验未完成）");
    } catch { missing.push(".mindspace-asr-ready.json（内容损坏）"); }
  }
  if (component.minimumWeightBytes) {
    const weight = path.join(target, "model.pt");
    if (fs.existsSync(weight) && fs.statSync(weight).size < component.minimumWeightBytes) missing.push("model.pt（下载未完成）");
  }
  return { ready: missing.length === 0, partial: present.length > 0 && missing.length > 0, missing, path: target };
}

function sha256(file) {
  const digest = crypto.createHash("sha256");
  const descriptor = fs.openSync(file, "r");
  const buffer = Buffer.allocUnsafe(4 * 1024 * 1024);
  try {
    let count;
    while ((count = fs.readSync(descriptor, buffer, 0, buffer.length, null)) > 0) digest.update(buffer.subarray(0, count));
  } finally { fs.closeSync(descriptor); }
  return digest.digest("hex");
}

function safeFile(root, relative) {
  const base = path.resolve(root);
  const target = path.resolve(base, relative);
  if (target !== base && !target.startsWith(`${base}${path.sep}`)) throw new Error(`组件包含不安全路径：${relative}`);
  return target;
}

function describeError(error) {
  const message = String(error?.message || error || "未知错误");
  const cause = error?.cause;
  const detail = [cause?.code, cause?.message].filter(Boolean).join(" · ");
  return detail && !message.includes(detail) ? `${message}（${detail}）` : message;
}

function classifyError(error, stage = "downloading") {
  const message = describeError(error);
  const normalized = message.toLowerCase();
  const causeCode = String(error?.cause?.code || error?.code || "").toUpperCase();
  let code = "COMPONENT_FAILED";
  if (["ENOTFOUND", "EAI_AGAIN"].includes(causeCode) || /dns|域名|解析/.test(normalized)) code = "NETWORK_DNS";
  else if (["ECONNRESET", "ETIMEDOUT", "ECONNREFUSED"].includes(causeCode) || /timeout|超时|connection|网络/.test(normalized)) code = "NETWORK_CONNECTION";
  else if (/tls|certificate|证书/.test(normalized)) code = "NETWORK_TLS";
  else if (/http\s*404/.test(normalized)) code = "HTTP_404";
  else if (/http\s*403/.test(normalized)) code = "HTTP_403";
  else if (/sha-?256|哈希|hash/.test(normalized)) code = "CHECKSUM_MISMATCH";
  else if (/大小校验|下载不完整|size/.test(normalized)) code = "SIZE_MISMATCH";
  else if (causeCode === "ENOSPC" || /磁盘空间|enospc/.test(normalized)) code = "DISK_FULL";
  else if (["EACCES", "EPERM"].includes(causeCode) || /权限|拒绝访问/.test(normalized)) code = "PERMISSION_DENIED";
  else if (/解压|压缩包|archive|zip/.test(normalized)) code = "EXTRACT_FAILED";
  else if (/依赖/.test(normalized)) code = "DEPENDENCY_FAILED";
  return { code, stage, message };
}

function categoryFor(component) {
  return component.category || (component.id === "embedding" ? "base" : "voice");
}

async function fetchJson(fetchImpl, url, signal) {
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const response = await fetchImpl(url, { signal, headers: { "User-Agent": "Mindspace-Launcher/0.3.0" } });
      if (!response.ok) throw new Error(`组件清单请求失败：HTTP ${response.status}`);
      return response.json();
    } catch (error) {
      if (signal.aborted) throw error;
      lastError = error;
      if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, attempt * 400));
    }
  }
  throw lastError;
}

async function resolveFiles(component, fetchImpl, signal, source = "china") {
  const selected = componentForSource(component, source);
  if (selected.provider === "static") return selected.files.map((file) => staticFileForSource(file, source));
  if (selected.provider === "huggingface") {
    const endpoint = `https://huggingface.co/api/models/${selected.repo}/tree/main?recursive=true&expand=true`;
    const payload = await fetchJson(fetchImpl, endpoint, signal);
    return payload.filter((item) => item.type === "file" && (!selected.filter || selected.filter(item))).map((item) => ({
      path: item.path,
      size: Number(item.size || item.lfs?.size || 0),
      sha256: /^[a-f0-9]{64}$/i.test(item.lfs?.oid || "") ? item.lfs.oid.toLowerCase() : "",
      url: `https://huggingface.co/${selected.repo}/resolve/main/${encodeRepoPath(item.path)}`,
    }));
  }
  if (selected.provider === "modelscope") {
    const endpoint = `https://www.modelscope.cn/api/v1/models/${selected.repo}/repo/files?Revision=master&Recursive=True`;
    const payload = await fetchJson(fetchImpl, endpoint, signal);
    return payload.Data.Files.filter(
      (item) => item.Type === "blob" && (!selected.filter || selected.filter(item)),
    ).map((item) => ({
      path: String(item.Path).replace(/\\/g, "/"),
      size: Number(item.Size || 0),
      sha256: /^[a-f0-9]{64}$/i.test(item.Sha256 || "") ? item.Sha256.toLowerCase() : "",
      url: `https://www.modelscope.cn/models/${selected.repo}/resolve/master/${encodeRepoPath(item.Path)}`,
    }));
  }
  throw new Error(`不支持的组件来源：${selected.provider}`);
}

function createComponentManager(options) {
  const catalog = options.catalog || DEFAULT_COMPONENTS;
  const fetchImpl = options.fetch || global.fetch;
  const states = new Map();
  let active = "";
  let controller = null;

  function log(event, details = {}) {
    if (!options.logFile) return;
    try {
      fs.mkdirSync(path.dirname(options.logFile), { recursive: true });
      fs.appendFileSync(
        options.logFile,
        `${JSON.stringify({ at: new Date().toISOString(), event, ...details })}\n`,
      );
    } catch {}
  }

  function stateFor(component) {
    if (!states.has(component.id)) states.set(component.id, { status: "idle", progress: 0, downloadedBytes: 0, totalBytes: component.estimatedBytes || 0, speedBps: 0, message: "等待下载", error: "", operationId: "", errorCode: "", errorStage: "", startedAt: "", updatedAt: "" });
    return states.get(component.id);
  }

  function itemSnapshot(component) {
    const report = reportReady(options.rootPath(), component, options.resolveTarget);
    const state = stateFor(component);
    if (report.ready && !["downloading", "installing", "resolving", "verifying"].includes(state.status)) {
      return { ...component, category: categoryFor(component), filter: undefined, files: undefined, archives: undefined, official: undefined, ...state, ...report, status: "ready", progress: 100, message: "组件已就绪", error: "" };
    }
    const message = report.partial && !["downloading", "installing", "resolving", "verifying"].includes(state.status)
      ? "上次安装未完成；点击继续将复用现有文件并补齐依赖"
      : state.message;
    return { ...component, category: categoryFor(component), filter: undefined, files: undefined, archives: undefined, official: undefined, ...state, message, ...report };
  }

  function snapshot() {
    return { active, downloadSource: normalizeDownloadSource(options.getDownloadSource?.()), items: catalog.map(itemSnapshot) };
  }

  function setState(component, patch) {
    Object.assign(stateFor(component), patch, { updatedAt: new Date().toISOString() });
  }

  function updateProgress(component, downloadedBytes, totalBytes, transfer, message = "正在下载") {
    const elapsed = Math.max(0.25, (Date.now() - transfer.startedAt) / 1000);
    setState(component, {
      status: "downloading",
      downloadedBytes,
      totalBytes,
      progress: totalBytes ? Math.min(99.8, downloadedBytes / totalBytes * 100) : 0,
      speedBps: Math.max(0, transfer.bytes / elapsed),
      message,
      error: "",
    });
  }

  async function downloadFile(component, file, targetRoot, completedBytes, totalBytes, transfer) {
    const target = safeFile(targetRoot, file.path);
    const partial = `${target}.partial`;
    fs.mkdirSync(path.dirname(target), { recursive: true });
    if (fs.existsSync(target) && fs.statSync(target).size === file.size) {
      if (!file.sha256 || sha256(target) === file.sha256) {
        updateProgress(component, completedBytes + file.size, totalBytes, transfer, `已校验 ${path.basename(file.path)}`);
        return file.size;
      }
    }
    if (fs.existsSync(partial) && fs.statSync(partial).size > file.size) fs.rmSync(partial, { force: true });
    let offset = fs.existsSync(partial) ? fs.statSync(partial).size : 0;
    const headers = { "User-Agent": "Mindspace-Launcher/0.3.0" };
    if (offset) headers.Range = `bytes=${offset}-`;
    let response = await fetchImpl(file.url, { signal: controller.signal, headers, redirect: "follow" });
    if (offset && response.status !== 206) {
      fs.rmSync(partial, { force: true });
      offset = 0;
      response = await fetchImpl(file.url, { signal: controller.signal, headers: { "User-Agent": headers["User-Agent"] }, redirect: "follow" });
    }
    if (!response.ok || !response.body) throw new Error(`下载 ${file.path} 失败：HTTP ${response.status}`);
    const output = fs.createWriteStream(partial, { flags: offset ? "a" : "w" });
    let current = offset;
    try {
      for await (const chunk of response.body) {
        if (controller.signal.aborted) throw new Error("下载已取消");
        if (!output.write(chunk)) await new Promise((resolve) => output.once("drain", resolve));
        current += chunk.length;
        transfer.bytes += chunk.length;
        updateProgress(component, completedBytes + current, totalBytes, transfer, `正在下载 ${path.basename(file.path)}`);
      }
      await new Promise((resolve, reject) => output.end((error) => error ? reject(error) : resolve()));
    } catch (error) {
      output.destroy();
      throw error;
    }
    if (current !== file.size) throw new Error(`${file.path} 大小校验失败：${current} / ${file.size}`);
    setState(component, { status: "verifying", message: `正在校验 ${path.basename(file.path)}` });
    if (file.sha256 && sha256(partial) !== file.sha256) {
      fs.rmSync(partial, { force: true });
      throw new Error(`${file.path} SHA-256 校验失败`);
    }
    if (fs.existsSync(target)) fs.rmSync(target, { force: true });
    fs.renameSync(partial, target);
    return file.size;
  }

  async function downloadFileWithRetry(component, file, targetRoot, completedBytes, totalBytes, transfer) {
    let lastError;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        return await downloadFile(component, file, targetRoot, completedBytes, totalBytes, transfer);
      } catch (error) {
        if (controller?.signal.aborted) throw error;
        lastError = error;
        log("file.retry", {
          component: component.id,
          file: file.path,
          attempt,
          error: describeError(error),
        });
        if (attempt < 3) {
          setState(component, { status: "downloading", message: `${path.basename(file.path)} 下载中断，正在第 ${attempt + 1} 次续传…`, error: "" });
          await new Promise((resolve) => setTimeout(resolve, attempt * 600));
        }
      }
    }
    throw lastError;
  }

  async function download(id) {
    const component = catalog.find((item) => item.id === id);
    if (!component) throw new Error("未知下载组件");
    for (const dependencyId of component.dependencies || []) {
      const dependency = catalog.find((item) => item.id === dependencyId);
      if (!dependency) throw new Error(`${component.name} 缺少依赖定义：${dependencyId}`);
      if (!reportReady(options.rootPath(), dependency, options.resolveTarget).ready) await download(dependencyId);
      if (!reportReady(options.rootPath(), dependency, options.resolveTarget).ready) throw new Error(`${component.name} 的依赖未完成：${dependency.name}`);
    }
    if (active) throw new Error(`正在下载 ${active}，请等待或取消后再试`);
    if (reportReady(options.rootPath(), component, options.resolveTarget).ready) return snapshot();
    active = id;
    controller = new AbortController();
    const operationId = `${id}-${Date.now().toString(36)}-${crypto.randomBytes(3).toString("hex")}`;
    const downloadSource = normalizeDownloadSource(options.getDownloadSource?.());
    setState(component, { status: "resolving", progress: 0, downloadedBytes: 0, speedBps: 0, message: downloadSource === "china" ? "正在读取国内镜像清单…" : "正在读取官方源清单…", error: "", operationId, errorCode: "", errorStage: "", startedAt: new Date().toISOString() });
    log("component.start", { component: id, operation_id: operationId, provider: component.provider, repository: component.repo, source: downloadSource });
    try {
      const targetRoot = options.resolveTarget ? options.resolveTarget(component) : path.join(options.rootPath(), component.target);
      fs.mkdirSync(targetRoot, { recursive: true });
      let totalBytes = component.estimatedBytes || 0;
      if (typeof fs.statfsSync === "function") {
        const disk = fs.statfsSync(targetRoot);
        const free = Number(disk.bavail) * Number(disk.bsize);
        if (free < totalBytes + 512 * 1024 * 1024) throw new Error(`磁盘空间不足：需要至少 ${Math.ceil((totalBytes + 512 * 1024 * 1024) / 1024 / 1024)} MiB 可用空间`);
      }
      let fileCount = 0;
      if (component.provider === "installer") {
        if (!options.installComponent) throw new Error("Launcher 未配置运行时安装器");
        setState(component, {
          status: "installing",
          progress: 2,
          downloadedBytes: 0,
          totalBytes,
          message: `正在准备${component.id === "tts-runtime" ? " CosyVoice" : component.id === "gpt-sovits-runtime" ? " GPT-SoVITS" : " ASR CUDA"}运行时…`,
          error: "",
        });
        await options.installComponent(component, controller.signal, (progress, message) => {
          setState(component, {
            status: "installing",
            progress: Math.max(2, Math.min(99, progress)),
            downloadedBytes: Math.round(totalBytes * Math.max(0, Math.min(100, progress)) / 100),
            totalBytes,
            message,
            error: "",
          });
        });
      } else {
        const files = await resolveFiles(component, fetchImpl, controller.signal, downloadSource);
        if (!files.length || files.some((file) => !file.path || !Number.isSafeInteger(file.size) || file.size <= 0)) throw new Error("官方文件清单为空或包含无效文件");
        totalBytes = files.reduce((sum, file) => sum + file.size, 0);
        fileCount = files.length;
        log("component.resolved", { component: id, files: fileCount, bytes: totalBytes, source: downloadSource });
        const transfer = { startedAt: Date.now(), bytes: 0 };
        let completedBytes = 0;
        for (const file of files) completedBytes += await downloadFileWithRetry(component, file, targetRoot, completedBytes, totalBytes, transfer);
        if (component.archives?.length) {
          if (!options.finalizeComponent) throw new Error("Launcher 未配置组件解压器");
          setState(component, { status: "installing", progress: 99, message: "正在安全解压并安装模型…", error: "" });
          await options.finalizeComponent(component, targetRoot);
        }
      }
      const report = reportReady(options.rootPath(), component, options.resolveTarget);
      if (!report.ready) throw new Error(`下载完成但组件仍不完整：${report.missing.join("、")}`);
      const markerRoot = options.markerRoot || path.join(options.rootPath(), "runtime", "components");
      fs.mkdirSync(markerRoot, { recursive: true });
      fs.writeFileSync(path.join(markerRoot, `${id}.json`), `${JSON.stringify({ id, source: downloadSource, repository: componentForSource(component, downloadSource).repo || component.provider, downloaded_at: new Date().toISOString(), bytes: totalBytes, files: fileCount }, null, 2)}\n`);
      setState(component, { status: "ready", progress: 100, downloadedBytes: totalBytes, totalBytes, speedBps: 0, message: "下载、校验并安装完成", error: "" });
      log("component.ready", { component: id, operation_id: operationId, files: fileCount, bytes: totalBytes });
    } catch (error) {
      const cancelled = controller?.signal.aborted;
      const diagnosis = classifyError(error, stateFor(component).status || "downloading");
      setState(component, { status: cancelled ? "cancelled" : "error", speedBps: 0, message: cancelled ? "下载已取消，可继续断点续传" : "组件下载失败", error: cancelled ? "" : diagnosis.message, errorCode: cancelled ? "CANCELLED" : diagnosis.code, errorStage: diagnosis.stage });
      log(cancelled ? "component.cancelled" : "component.error", {
        component: id,
        operation_id: operationId,
        error_code: cancelled ? "CANCELLED" : diagnosis.code,
        stage: diagnosis.stage,
        error: cancelled ? "" : diagnosis.message,
      });
      if (!cancelled) throw error;
    } finally {
      active = "";
      controller = null;
    }
    return snapshot();
  }

  async function downloadAll() {
    for (const component of catalog) {
      if (component.optional) continue;
      if (!reportReady(options.rootPath(), component, options.resolveTarget).ready) {
        await download(component.id);
        if (stateFor(component).status === "cancelled") break;
      }
    }
    return snapshot();
  }

  function cancel(id) {
    if (!active || (id && active !== id)) return snapshot();
    controller?.abort();
    return snapshot();
  }

  return { snapshot, download, downloadAll, cancel };
}

module.exports = {
  DEFAULT_COMPONENTS,
  classifyError,
  createComponentManager,
  describeError,
  encodeRepoPath,
  normalizeDownloadSource,
  reportReady,
  resolveFiles,
  safeFile,
  sha256,
};
