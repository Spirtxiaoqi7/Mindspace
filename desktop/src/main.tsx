import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const services = {
  api: { title: "Mindspace Core", subtitle: "LangGraph · RAG · SSE", icon: "M", tone: "clay" },
  asr: { title: "实时聆听", subtitle: "FunASR · VAD · 打断", icon: "≋", tone: "teal" },
  tts: { title: "自然声音", subtitle: "可切换本地流式合成", icon: "◒", tone: "sage" },
} as const;

const bundledReleaseHistory: ReleaseAnnouncement[] = [
  { version: "0.5.6", published_at: "2026-07-23", title: "档案写回与时间、联网判断修复", summary: ["档案支持表单编辑与版本化保存", "有记忆价值时才抽取，AI 自述采用本轮逐字证据", "明确联网请求在规划降级时仍会执行", "日期、星期与周末由服务端直接计算"] },
  { version: "0.5.5", published_at: "2026-07-23", title: "中文整句复核与 CUDA 调度", summary: ["Paraformer 实时字幕后由 Fun-ASR Nano 做中文整句复核", "含糊尾词、句末与播放场景使用动态断句", "CUDA 推理单通道且流式优先，缺失或失败自动回退", "本地 TTS 等待 ASR 模型加载完成后再启动"] },
  { version: "0.5.4", published_at: "2026-07-23", title: "低延迟与断线恢复", summary: ["本机状态改为能力触发时采集，向量查询复用已有结果", "只读外部能力安全并行，HTTP 连接统一池化复用", "流式对话支持事件续传，前端按帧批量更新", "启动器合并并发启动、并行探测并关闭情绪模型链路"] },
  { version: "0.5.3", published_at: "2026-07-22", title: "角色优先与事实约束", summary: ["角色设定提升为首条 System，回复首先从人物关系与性格出发", "AI 只表示存在媒介，不再触发通用问答口吻", "召回未获权威来源确认时不得引用为用户偏好或共同记忆", "JSON 协议压缩后置，与可见角色回复分离"] },
  { version: "0.5.2", published_at: "2026-07-22", title: "实时识别与智能词表", summary: ["新增自适应噪声基线，呼吸和瞬时噪声不再直接打断", "语音片段合并后发送，生成期间补充话与原输入组成同一轮", "播放中未确认打断的有效语音会延后保留", "新增在线识别词表与人物、专名动态强化"] },
  { version: "0.5.1", published_at: "2026-07-21", title: "时间感知与自然续接", summary: ["文字与语音统一记录服务端时间和对话间隔", "新增可配置的沉默后自然续接，不伪造用户指令", "语音打断记录实际播放位置，并区分候选声音与确认人声", "播放结束后自动恢复短回复灵敏度"] },
  { version: "0.5.0", published_at: "2026-07-21", title: "启动器分类与可靠下载", summary: ["首页改为状态概览与可伸缩分类", "基础环境按依赖顺序自动安装并复用已就绪组件", "下载错误提供错误码、操作编号与脱敏诊断报告", "包含 GPT-SoVITS V4 韵律与启动修复"] },
  { version: "0.4.6", published_at: "2026-07-20", title: "语音与存储修复", summary: ["修复残留 ASR CUDA 环境误判", "支持续装本地语音依赖与模型", "支持迁移环境、模型和数据到其他磁盘"] },
  { version: "0.4.5", published_at: "2026-07-20", title: "更新与本地语音稳定性", summary: ["修复 TTS 本地链路选择回跳", "恢复 ASR 与 CosyVoice 共享运行时", "普通更新优先使用 Core 原子替换"] },
  { version: "0.4.4", published_at: "2026-07-20", title: "零环境安装修复", summary: ["修复更新误清理私有环境", "核心服务优先启动", "组件下载支持续传和失败恢复"] },
];

const sleep = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
const formatBytes = (value: number) => {
  if (!value) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
};

function App() {
  const [data, setData] = useState<LauncherSnapshot>();
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("正在感知本地环境…");
  const [update, setUpdate] = useState<UpdateSnapshot>();
  const [updateChannel, setUpdateChannel] = useState("stable");
  const [runtime, setRuntime] = useState<RuntimeSnapshot>({ active: "", ready: false, system: {}, items: [] });
  const [downloadSource, setDownloadSource] = useState<"china" | "official">("china");
  const [voices, setVoices] = useState<TtsVoiceSnapshot>({ provider: "", current: "v4-changli", items: [] });
  const [voiceFranchise, setVoiceFranchise] = useState("");
  const [voiceChoice, setVoiceChoice] = useState("");
  const voicePickerInitialized = useRef(false);
  const [proxy, setProxy] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    try { return { base: false, capabilities: false, downloads: false, maintenance: false, ...JSON.parse(localStorage.getItem("mindspace.launcher-panels") || "{}") }; }
    catch { return { base: false, capabilities: false, downloads: false, maintenance: false }; }
  });
  const [announcementOpen, setAnnouncementOpen] = useState(() => new URLSearchParams(window.location.search).has("announcement"));
  const [announcementView, setAnnouncementView] = useState<"update" | "history">("history");
  const shownRelease = useRef("");
  const [launcherFontScale, setLauncherFontScale] = useState(() => {
    const raw = localStorage.getItem("mindspace.launcher-font-scale");
    const stored = raw === null ? Number.NaN : Number(raw);
    return Number.isFinite(stored) ? Math.max(1, Math.min(1.5, stored)) : 1.2;
  });
  const refresh = useCallback(async () => {
    const next = await window.launcher.snapshot();
    setData(next); setRuntime(next.runtime); setDownloadSource(next.runtime.downloadSource || "china"); setVoices(next.voices);
    return next;
  }, []);

  useEffect(() => {
    refresh().then((next) => setNotice(next.workspace.error || next.workspace.message || "本地状态已同步"));
    window.launcher.update("snapshot").then((next) => { setUpdate(next); setUpdateChannel(next.channel); });
    window.launcher.runtime("snapshot").then((next) => { setRuntime(next); setDownloadSource(next.downloadSource || "china"); });
    window.launcher.voice("snapshot").then(setVoices);
    const timer = window.setInterval(refresh, 3000);
    const updateTimer = window.setInterval(() => window.launcher.update("snapshot").then(setUpdate), 5000);
    return () => { window.clearInterval(timer); window.clearInterval(updateTimer); };
  }, [refresh]);

  useEffect(() => {
    const interval = runtime.active ? 500 : 3500;
    const timer = window.setInterval(() => window.launcher.runtime("snapshot").then(setRuntime), interval);
    return () => window.clearInterval(timer);
  }, [runtime.active]);

  useEffect(() => { localStorage.setItem("mindspace.launcher-panels", JSON.stringify(expanded)); }, [expanded]);

  useEffect(() => {
    const applyTypography = () => {
      const viewportBonus = window.innerWidth >= 1700 && window.innerHeight >= 850
        ? 0.12
        : window.innerWidth >= 1400 && window.innerHeight >= 760 ? 0.06 : 0;
      const effective = Math.min(1.62, launcherFontScale + viewportBonus);
      document.documentElement.style.fontSize = `${16 * effective}px`;
      document.documentElement.dataset.viewportTypography = viewportBonus ? "expanded" : "normal";
    };
    localStorage.setItem("mindspace.launcher-font-scale", String(launcherFontScale));
    applyTypography();
    window.addEventListener("resize", applyTypography);
    return () => window.removeEventListener("resize", applyTypography);
  }, [launcherFontScale]);

  useEffect(() => {
    if (!update || update.updateKind === "none" || (!update.coreAvailable && !update.launcherAvailable)) return;
    if (!update.releaseId || shownRelease.current === update.releaseId) return;
    if (!["available", "downloading", "verifying", "downloaded", "paused"].includes(update.status)) return;
    shownRelease.current = update.releaseId;
    setAnnouncementView("update");
    setAnnouncementOpen(true);
  }, [update]);

  const onlineCount = useMemo(
    () => Object.values(data?.services || {}).filter((item) => item.online).length,
    [data],
  );
  const allOnline = onlineCount === 3;
  const readyModels = data?.models.filter((item) => item.ready).length || 0;
  const baseIds = useMemo(() => new Set(["powershell", "git", "uv", "python", "core-venv", "embedding"]), []);
  const baseItems = useMemo(() => runtime.items.filter((item) => item.category === "base" || baseIds.has(item.id)), [baseIds, runtime.items]);
  const capabilityItems = useMemo(() => runtime.items.filter((item) => item.category !== "base" && item.category !== "voice" && !baseIds.has(item.id)), [baseIds, runtime.items]);
  const failedItems = useMemo(() => runtime.items.filter((item) => item.status === "error" || Boolean(item.error)), [runtime.items]);
  const baseReady = baseItems.filter((item) => item.ready).length;
  const capabilityReady = capabilityItems.filter((item) => item.ready).length;
  const baseProgress = runtime.pipeline?.progress ?? (baseItems.length ? baseItems.reduce((sum, item) => sum + (item.ready ? 100 : item.progress || 0), 0) / baseItems.length : 0);
  const voiceFranchises = useMemo(() => Array.from(new Set(voices.items.map((voice) => voice.franchise))), [voices.items]);
  const franchiseVoices = useMemo(() => voices.items.filter((voice) => voice.franchise === voiceFranchise), [voiceFranchise, voices.items]);
  const chosenVoice = useMemo(() => voices.items.find((voice) => voice.id === voiceChoice) || franchiseVoices[0], [franchiseVoices, voiceChoice, voices.items]);
  const installedVoices = voices.items.filter((voice) => voice.ready).length;
  const activeRuntimeItem = runtime.items.find((item) => item.id === runtime.active);
  const chosenVoiceInstalling = Boolean(chosenVoice && (
    busy === `voice-install:${chosenVoice.id}` || runtime.active === chosenVoice.componentId
  ));
  const chosenVoiceProgress = chosenVoice?.ready
    ? 100
    : chosenVoiceInstalling && activeRuntimeItem
      ? activeRuntimeItem.progress || chosenVoice?.progress || 0
      : chosenVoice?.progress || 0;
  const chosenVoiceProgressText = chosenVoice?.ready
    ? "下载完成 · 已通过完整性校验"
    : chosenVoiceInstalling
      ? `${activeRuntimeItem?.id === chosenVoice?.componentId ? "正在下载人物音色" : `正在准备公共依赖：${activeRuntimeItem?.name || "等待调度"}`} · ${chosenVoiceProgress.toFixed(1)}%${activeRuntimeItem?.speedBps ? ` · ${formatBytes(activeRuntimeItem.speedBps)}/s` : ""}`
      : chosenVoice?.error || chosenVoice?.message || "尚未下载";

  useEffect(() => {
    if (!voices.items.length || voicePickerInitialized.current) return;
    const current = voices.items.find((voice) => voice.id === voices.current) || voices.items[0];
    setVoiceFranchise(current.franchise);
    setVoiceChoice(current.id);
    voicePickerInitialized.current = true;
  }, [voices.current, voices.items]);

  useEffect(() => {
    if (failedItems.some((item) => baseIds.has(item.id))) setExpanded((value) => ({ ...value, base: true }));
    if (failedItems.some((item) => !baseIds.has(item.id))) setExpanded((value) => ({ ...value, capabilities: true }));
    if (runtime.active) setExpanded((value) => ({ ...value, [baseIds.has(runtime.active) ? "base" : "capabilities"]: true }));
  }, [baseIds, failedItems, runtime.active]);

  const togglePanel = (id: string) => setExpanded((value) => ({ ...value, [id]: !value[id] }));

  async function serviceAction(service: string, action: string) {
    setBusy(`${service}:${action}`);
    try {
      if (service === "asr" && action === "start") {
        if (runtime.system.nvidia === false) throw new Error("本地实时语音需要兼容的 NVIDIA 显卡与驱动");
        for (const id of ["asr-runtime", "asr", "vad", "punc"]) {
          const item = runtime.items.find((candidate) => candidate.id === id);
          if (item && !item.ready) {
            setNotice(`${item.partial ? "正在继续修复" : "正在安装"}${item.name}；已下载内容会被复用…`);
            const next = await window.launcher.runtime(item.status === "error" || item.partial ? "retry" : "install", id);
            setRuntime(next);
          }
        }
      }
      const result = await window.launcher.action(service, action);
      setNotice(result.ok ? `${services[service as keyof typeof services].title} 操作已提交` : result.error || "操作失败");
      await sleep(700);
      await refresh();
    } catch (error) {
      setNotice((error as Error).message || "操作失败");
      await refresh();
    } finally { setBusy(""); }
  }

  async function launchMindspace() {
    setBusy("launch");
    let launchWarnings: string[] = [];
    if (!runtime.ready) {
      setNotice("正在按顺序准备应用私有环境，请保持网络连接…");
      try {
        const next = await window.launcher.runtime("install-all");
        setRuntime(next);
      } catch (error) {
        setNotice((error as Error).message || "环境初始化失败，请查看对应组件和日志");
        setBusy("");
        return;
      }
    }
    if (!allOnline) {
      setNotice("正在依次唤醒声音、聆听与核心服务…");
      const result = await window.launcher.all("start");
      if (!result.ok) {
        setNotice(result.error || "启动失败，请查看日志");
        setBusy("");
        return;
      }
      launchWarnings = result.warnings || [];
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const next = await refresh();
        if (next.services.api?.online) break;
        await sleep(500);
      }
    }
    setNotice(launchWarnings.length ? `Mindspace 核心已就绪；${launchWarnings.join("；")}` : "Mindspace 已准备好");
    await window.launcher.open("app");
    setBusy("");
  }

  async function maintenance(action: string, label: string) {
    setBusy(action);
    const result = await window.launcher.maintenance(action);
    setNotice(result.ok ? `${label}已在后台运行` : result.error || "启动失败");
    setBusy("");
  }

  async function stopAll() {
    setBusy("stop-all");
    const result = await window.launcher.all("stop");
    setNotice(result.ok ? "已停止由启动器创建的服务" : result.error || "停止失败");
    await sleep(500);
    await refresh();
    setBusy("");
  }

  async function updateAction(action: string) {
    setBusy(`update:${action}`);
    try {
      const next = action === "configure"
        ? await window.launcher.update(action, { channel: updateChannel })
        : await window.launcher.update(action);
      setUpdate(next); setNotice(next.message || "更新操作完成");
    } catch (error) {
      setNotice((error as Error).message); setUpdate(await window.launcher.update("snapshot"));
    } finally { setBusy(""); }
  }

  async function runtimeAction(action: "install" | "install-all" | "cancel" | "retry" | "repair", id = "") {
    try {
      if (action !== "cancel") setNotice(action === "install-all" ? "正在初始化全部基础环境…" : "正在准备运行时组件…");
      const next = await window.launcher.runtime(action, id);
      setRuntime(next); await refresh();
      setNotice(action === "cancel" ? "安装已取消，下载进度已保留" : next.ready ? "零环境运行时已准备完成" : "组件操作完成");
    } catch (error) {
      setNotice((error as Error).message);
      setRuntime(await window.launcher.runtime("snapshot"));
    }
  }

  async function selectVoice(id: string) {
    const voice = voices.items.find((item) => item.id === id);
    setBusy(`voice:${id}`);
    setNotice(`正在切换到 ${voice?.label || id}…`);
    try {
      const next = await window.launcher.voice("select", id);
      setVoices(next);
      await refresh();
      setNotice(next.error || next.warning || `已切换到 ${voice?.label || id}`);
    } catch (error) {
      setNotice((error as Error).message || "音色切换失败");
      setVoices(await window.launcher.voice("snapshot"));
    } finally { setBusy(""); }
  }

  async function installVoice(id: string) {
    const voice = voices.items.find((item) => item.id === id);
    setBusy(`voice-install:${id}`);
    setNotice(`正在单独下载 ${voice?.label || id}；公共模型与运行时只部署一次…`);
    try {
      const next = await window.launcher.voice("install", id);
      setVoices(next);
      await refresh();
      setNotice(`${voice?.label || id} 已下载并通过完整性校验；需要时可设为当前音色`);
    } catch (error) {
      setNotice((error as Error).message || "人物音色下载失败");
      setVoices(await window.launcher.voice("snapshot"));
      setRuntime(await window.launcher.runtime("snapshot"));
    } finally { setBusy(""); }
  }

  async function saveProxy() {
    try {
      await window.launcher.proxy(proxy);
      setNotice(proxy ? "下载代理已保存" : "已恢复跟随 Windows 系统代理");
    } catch (error) { setNotice((error as Error).message); }
  }

  async function saveDownloadSource(source: "china" | "official") {
    setBusy("download-source");
    try {
      const next = await window.launcher.source(source);
      setRuntime(next);
      setDownloadSource(next.downloadSource || source);
      setNotice(source === "china" ? "已切换到国内镜像；后续下载使用 ModelScope 与阿里云" : "已切换到官方源；后续模型与依赖下载使用 Hugging Face、PyPI 等上游地址");
    } catch (error) {
      setNotice((error as Error).message || "下载源切换失败");
    } finally { setBusy(""); }
  }

  async function selectStorage() {
    setBusy("storage");
    setNotice("请选择磁盘或文件夹；启动器会创建独立的 Mindspace 子目录…");
    try {
      const next = await window.launcher.selectStorage();
      setData(next);
      setNotice(next.storage?.message || "已取消存储位置变更");
    } catch (error) {
      setNotice((error as Error).message || "存储位置迁移失败，原目录未变更");
    } finally { setBusy(""); }
  }

  async function exportDiagnostics() {
    setBusy("diagnostics");
    try {
      const result = await window.launcher.diagnostics();
      setNotice(result.ok ? `诊断报告已生成：${result.path}` : result.error || "诊断报告生成失败");
    } catch (error) {
      setNotice((error as Error).message || "诊断报告生成失败");
    } finally { setBusy(""); }
  }

  const releaseHistory = update?.releaseHistory?.length ? update.releaseHistory : bundledReleaseHistory;
  const currentAnnouncement = releaseHistory.find((entry) => entry.version === update?.latestVersion) || {
    version: update?.latestVersion || "",
    published_at: "",
    title: update?.releaseTitle || "Mindspace 版本更新",
    summary: (update?.releaseNotes || "稳定性与体验更新").split(/\r?\n/).filter(Boolean),
  };

  async function announcementUpdateAction() {
    if (!update) return;
    if (update.downloaded || update.status === "downloaded") await updateAction("install");
    else if (["available", "paused", "error"].includes(update.status)) await updateAction("download");
  }

  const renderComponents = (items: RuntimeComponentState[]) => <div className="component-list grouped-list">{items.map((item) => {
    const running = runtime.active === item.id;
    const total = item.displayEstimatedBytes === false ? 0 : item.totalBytes || 0;
    const detail = item.unavailableReason || item.error || item.message;
    return <article id={`component-${item.id}`} className={`component-row ${item.ready ? "ready" : ""} ${item.error ? "failed" : ""}`} key={item.id}>
      <span className="component-check">{item.ready ? "✓" : running ? "↓" : item.error ? "!" : "○"}</span>
      <div className="component-copy">
        <strong>{item.name}{item.optional ? " · 可选" : ""}</strong>
        <small>{item.description}{total ? ` · ${formatBytes(total)}` : ""}</small>
        <div className="component-progress"><i style={{ width: `${item.ready ? 100 : item.progress || 0}%` }} /></div>
        <span>{detail}{running ? item.speedBps > 0 ? ` · ${formatBytes(item.speedBps)}/s · ${item.progress.toFixed(1)}%` : ` · ${item.progress.toFixed(1)}%` : ""}</span>
        {item.error && <span className="diagnostic-code">错误码 {item.errorCode || "UNKNOWN"} · 阶段 {item.errorStage || item.status}{item.operationId ? ` · 操作 ${item.operationId}` : ""}</span>}
      </div>
      {running ? <button onClick={() => void runtimeAction("cancel", item.id)}>取消</button> : <button disabled={Boolean(runtime.active) || item.ready || item.hardwareAvailable === false} onClick={() => void runtimeAction(item.status === "error" || item.status === "cancelled" ? "retry" : "install", item.id)}>{item.ready ? "已部署" : item.status === "cancelled" || item.downloadedBytes ? "继续" : item.kind === "model" || item.downloadRequired ? "下载" : item.bundled ? "本地部署" : "安装"}</button>}
    </article>;
  })}</div>;

  return <div className="app-shell">
    <header className="titlebar">
      <div className="brand"><span className="brand-mark">M</span><div><strong>Mindspace</strong><small>LOCAL COMPANION</small></div></div>
      <nav><button className="active">概览</button><button onClick={() => { setAnnouncementView("history"); setAnnouncementOpen(true); }}>公告</button><button onClick={() => window.launcher.open("models")}>模型</button></nav>
      <div className="font-controls" aria-label="启动器字体大小">
        <button aria-label="减小启动器字体" title="减小字体" disabled={launcherFontScale <= 1} onClick={() => setLauncherFontScale((value) => Math.max(1, Number((value - 0.1).toFixed(1))))}>A−</button>
        <span title="启动器字体比例">{Math.round(launcherFontScale * 100)}%</span>
        <button aria-label="放大启动器字体" title="放大字体" disabled={launcherFontScale >= 1.5} onClick={() => setLauncherFontScale((value) => Math.min(1.5, Number((value + 0.1).toFixed(1))))}>A+</button>
      </div>
      <span className="title-status"><i className={runtime.ready && allOnline ? "online" : ""} />{!runtime.ready ? "环境待初始化" : allOnline ? "全部就绪" : `${onlineCount}/3 服务在线`}</span>
    </header>

    <main>
      {(update?.status === "available" || update?.status === "downloaded" || update?.mandatory) && <section className={`update-banner ${update.mandatory ? "mandatory" : ""}`}>
        <div><strong>{update.mandatory ? "需要更新" : "发现新版本"}</strong><span>{update.updateKind === "launcher" ? "Launcher" : "Mindspace Core"} {update.latestVersion} · {update.releaseNotes || "包含稳定性和功能改进"}</span></div>
        {update.downloaded ? <button onClick={() => updateAction("install")}>{update.updateKind === "launcher" ? "静默更新并重启" : "应用更新"}</button> : <button onClick={() => updateAction("download")}>后台下载</button>}
      </section>}
      <section className="hero">
        <div className="hero-copy">
          <span className="eyebrow">YOUR PRIVATE AI SPACE</span>
          <h1>一次点击，<br />唤醒你的 <em>Mindspace</em></h1>
          <p>记忆与识别在本机准备，语音合成可由云端 API 承接。启动器负责让服务安静地运转，并安全同步新版本。</p>
          <div className="hero-actions"><button className="primary launch" disabled={Boolean(busy)} onClick={launchMindspace}>{busy === "launch" ? <><i className="spinner" />{runtime.ready ? "正在启动" : "正在初始化"}</> : !runtime.ready ? <>一键初始化并进入 <b>→</b></> : allOnline ? <>进入 Mindspace <b>↗</b></> : <>启动并进入 <b>→</b></>}</button><button className="quiet" onClick={() => window.launcher.open("root")}>打开应用目录</button></div>
          <span className="notice-line"><i />{notice}</span>
        </div>
        <div className="hero-visual">
          <div className="aura aura-one" /><div className="aura aura-two" />
          <div className="portrait"><img src="./avatar-ai-default.webp" alt="Mindspace 角色头像" /></div>
          <span className="floating-chip chip-memory"><i>◇</i><b>记忆已连接</b><small>本地持久化</small></span>
          <span className="floating-chip chip-voice"><i>≋</i><b>声音已准备</b><small>实时可打断</small></span>
        </div>
      </section>

      {failedItems.length > 0 && <section className="failure-banner" role="alert">
        <div><strong>{failedItems[0].name}未完成</strong><span>{failedItems[0].errorCode || "INSTALL_FAILED"} · {failedItems[0].error || "打开详情查看失败原因"}</span></div>
        <div><button onClick={() => { setExpanded((value) => ({ ...value, [baseIds.has(failedItems[0].id) ? "base" : "capabilities"]: true })); window.setTimeout(() => document.getElementById(`component-${failedItems[0].id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 60); }}>定位组件</button><button onClick={() => window.launcher.open("logs")}>打开日志</button><button onClick={() => void exportDiagnostics()}>导出诊断报告</button></div>
      </section>}

      <section className="runtime-panel overview-panel">
        <div className="panel-heading"><div><span className="eyebrow">RUNTIME</span><h2>本地服务</h2></div><div className="panel-controls"><button className="refresh" disabled={Boolean(busy)} onClick={stopAll}>全部停止</button><button className="refresh" onClick={() => void refresh()}>↻ 刷新</button></div></div>
        <div className="service-grid service-strip">{Object.entries(services).map(([id, item]) => {
          const online = data?.services[id]?.online || false;
          const remoteTts = id === "tts" && !["cosyvoice", "gpt-sovits"].includes(data?.ttsProvider || "");
          const asrRuntime = runtime.items.find((candidate) => candidate.id === "asr-runtime");
          const asrNeedsSetup = id === "asr" && !asrRuntime?.ready;
          const asrUnavailable = id === "asr" && runtime.system.nvidia === false;
          return <article className={`service-card ${online ? "online" : ""}`} key={id}>
            <span className={`service-icon ${item.tone}`}>{item.icon}</span>
            <div><strong>{item.title}</strong><small>{remoteTts ? "SiliconFlow · 流式 API" : id === "tts" && data?.ttsProvider === "gpt-sovits" ? `${voices.items.find((voice) => voice.id === voices.current)?.label || "GPT-SoVITS"} · 本地流式` : item.subtitle}</small></div>
            <span className="service-state"><i />{remoteTts ? "API 托管" : online ? "运行中" : "未启动"}</span>
            <button disabled={Boolean(busy) || remoteTts || asrUnavailable} onClick={() => serviceAction(id, online ? "restart" : "start")}>{remoteTts ? "无需本地模型" : asrUnavailable ? "需要 NVIDIA" : online ? "重启" : asrNeedsSetup ? asrRuntime?.partial ? "继续修复并启动" : "安装并启动" : "启动"}</button>
          </article>;
        })}</div>
      </section>

      <section className={`accordion-panel ${expanded.base ? "expanded" : ""} ${failedItems.some((item) => baseIds.has(item.id)) ? "has-error" : ""}`}>
        <button className="accordion-heading" aria-expanded={Boolean(expanded.base)} onClick={() => togglePanel("base")}><span className="accordion-icon">01</span><span><b>基础环境</b><small>{!baseItems.length ? "正在检测私有运行环境" : runtime.pipeline?.status === "running" ? `正在准备 ${runtime.pipeline.currentName}` : baseReady === baseItems.length ? "全部就绪，更新不会重复安装" : `${baseReady}/${baseItems.length} 项已就绪`}</small></span><span className="accordion-meter"><i style={{ width: `${baseProgress}%` }} /></span><strong>{baseItems.length ? `${baseProgress.toFixed(0)}%` : "检测中"}</strong><em>{expanded.base ? "收起" : "展开"}</em></button>
        {expanded.base && <div className="accordion-body">
          <div className="environment-actions"><button className="component-all" disabled={Boolean(runtime.active) || runtime.ready} onClick={() => void runtimeAction("install-all")}>{runtime.ready ? "基础环境已就绪" : runtime.active ? `正在安装 ${runtime.pipeline?.currentName || "组件"}` : "按顺序初始化基础环境"}</button></div>
          <p className="component-note">顺序执行系统预检 → PowerShell → MinGit → uv → Python → 核心环境 → 中文向量模型。安装包已携带的工具直接本地部署，已通过校验的步骤自动跳过。</p>
          <div className="runtime-preflight"><span className={runtime.system.supported ? "ready" : "failed"}>系统 {runtime.system.windowsRelease || "检测中"} · {runtime.system.supported ? "Win10/11 x64" : "不受支持"}</span><span className={runtime.system.writable ? "ready" : "failed"}>目录{runtime.system.writable ? "可写" : "不可写"}</span><span>{formatBytes(runtime.system.freeBytes || 0)} 可用空间</span></div>
          {renderComponents(baseItems)}
        </div>}
      </section>

      <section className={`accordion-panel ${expanded.capabilities ? "expanded" : ""} ${failedItems.some((item) => !baseIds.has(item.id)) ? "has-error" : ""}`}>
        <button className="accordion-heading" aria-expanded={Boolean(expanded.capabilities)} onClick={() => togglePanel("capabilities")}><span className="accordion-icon">02</span><span><b>语音与模型能力</b><small>{!data ? "正在检测模型与语音能力" : `RAG ${readyModels}/${data.models.length || 5} · 本地能力 ${capabilityReady}/${capabilityItems.length} · 当前 ${voices.items.find((voice) => voice.id === voices.current)?.label || data.ttsProvider || "云端 TTS"}`}</small></span><strong>{runtime.system.nvidia === undefined ? "检测中" : runtime.system.nvidia ? "NVIDIA 可用" : "本地语音可选"}</strong><em>{expanded.capabilities ? "收起" : "展开"}</em></button>
        {expanded.capabilities && <div className="accordion-body">
          <div className="model-pills">{data?.models.map((model) => <span className={model.ready ? "ready" : "missing"} key={model.id}><i>{model.optional ? "○" : model.ready ? "✓" : "!"}</i>{model.name}</span>)}</div>
          <p className="component-note">ASR、CosyVoice 与 GPT-SoVITS 为硬件可选能力，不阻塞文字聊天和云端 TTS。V4 公共模型及 CUDA 运行时只部署一次；人物音色在下方按需单独下载。</p>
          {renderComponents(capabilityItems)}
          <div className="voice-subsection">
            <div className="panel-heading"><h3>人物音色</h3><span className="voice-current">已下载 {installedVoices}/{voices.items.length} · {voices.provider === "gpt-sovits" ? `当前 ${voices.items.find((voice) => voice.id === voices.current)?.label || voices.current}` : "当前使用其他 TTS"}</span></div>
            <div className="voice-picker">
              <label><span>作品分类</span><select value={voiceFranchise} onChange={(event) => { const franchise = event.target.value; const first = voices.items.find((voice) => voice.franchise === franchise); setVoiceFranchise(franchise); setVoiceChoice(first?.id || ""); }} disabled={!voiceFranchises.length}>{voiceFranchises.map((franchise) => <option key={franchise} value={franchise}>{franchise}</option>)}</select></label>
              <label><span>人物音色</span><select value={chosenVoice?.id || ""} onChange={(event) => setVoiceChoice(event.target.value)} disabled={!franchiseVoices.length}>{franchiseVoices.map((voice) => <option key={voice.id} value={voice.id}>{voice.character} · {voice.family === "v4" ? "V4" : "V2ProPlus"} · {voice.releaseYear}</option>)}</select></label>
              {chosenVoice && <article className={`voice-selection ${chosenVoice.ready ? "ready" : ""} ${voices.provider === "gpt-sovits" && voices.current === chosenVoice.id ? "selected" : ""}`}>
                <div><span>{chosenVoice.franchise} · 归档内部已核验</span><strong>{chosenVoice.label}</strong><small>{chosenVoice.ready ? "人物音色已安装" : `${formatBytes(chosenVoice.estimatedBytes)} · 仅下载这一人物`} · {chosenVoice.engine}</small><div className={`voice-download-progress ${chosenVoice.ready ? "complete" : ""}`} aria-label={`${chosenVoice.label} 下载进度 ${chosenVoiceProgress.toFixed(0)}%`}><i style={{ width: `${chosenVoiceProgress}%` }} /></div><small className="voice-progress-label">{chosenVoiceProgressText}</small>{chosenVoice.error && <em>{chosenVoice.error}</em>}</div>
                <div className="voice-actions"><button className="voice-source" onClick={() => void window.launcher.external(chosenVoice.sourceUrl)}>来源</button>{chosenVoiceInstalling ? <button onClick={() => void runtimeAction("cancel", runtime.active || chosenVoice.componentId)}>取消 {chosenVoiceProgress.toFixed(0)}%</button> : !chosenVoice.ready ? <button disabled={Boolean(busy) || Boolean(runtime.active) || runtime.system.nvidia === false} onClick={() => void installVoice(chosenVoice.id)}>单独下载</button> : <button disabled={Boolean(busy) || Boolean(runtime.active) || (voices.provider === "gpt-sovits" && voices.current === chosenVoice.id)} onClick={() => void selectVoice(chosenVoice.id)}>{voices.provider === "gpt-sovits" && voices.current === chosenVoice.id ? "使用中" : "设为当前"}</button>}</div>
              </article>}
            </div>
          </div>
        </div>}
      </section>

      <section className={`accordion-panel ${expanded.downloads ? "expanded" : ""}`}>
        <button className="accordion-heading" aria-expanded={Boolean(expanded.downloads)} onClick={() => togglePanel("downloads")}><span className="accordion-icon">03</span><span><b>下载与存储</b><small>{downloadSource === "china" ? "国内镜像" : "官方境外源"} · {data?.home || "正在检测目录"}</small></span><strong>{runtime.system.freeBytes ? `${formatBytes(runtime.system.freeBytes)} 可用` : "检测中"}</strong><em>{expanded.downloads ? "收起" : "展开"}</em></button>
        {expanded.downloads && <div className="accordion-body settings-grid"><div><label className="source-picker"><span>下载源</span><select value={downloadSource} disabled={Boolean(runtime.active) || busy === "download-source"} onChange={(event) => void saveDownloadSource(event.target.value as "china" | "official")}><option value="china">国内镜像（推荐）</option><option value="official">官方境外源</option></select></label><p className="component-note">切换只影响后续下载；已下载文件和断点缓存继续复用。</p></div><div><div className="proxy-row"><input value={proxy} onChange={(event) => setProxy(event.target.value)} placeholder="代理地址；留空跟随 Windows" /><button onClick={saveProxy}>保存代理</button></div><button className="component-all" disabled={Boolean(busy) || Boolean(runtime.active)} onClick={selectStorage}>{data?.storage?.active ? `迁移中 ${data.storage.progress}%` : "更改统一存储位置"}</button></div></div>}
      </section>

      <section className={`accordion-panel ${expanded.maintenance ? "expanded" : ""}`}>
        <button className="accordion-heading" aria-expanded={Boolean(expanded.maintenance)} onClick={() => togglePanel("maintenance")}><span className="accordion-icon">04</span><span><b>更新与维护</b><small>Core v{update?.currentVersion || "…"} · Launcher v{update?.launcherVersion || "…"} · {failedItems.length ? `${failedItems.length} 个异常` : "运行状态正常"}</small></span><strong>{update?.status === "available" ? `可更新 ${update.latestVersion}` : "稳定通道"}</strong><em>{expanded.maintenance ? "收起" : "展开"}</em></button>
        {expanded.maintenance && <div className="accordion-body">
          <div className="update-row official"><span className="official-source"><i />Mindspace 官方签名更新源</span><select value={updateChannel} onChange={(event) => setUpdateChannel(event.target.value)}><option value="stable">稳定通道</option><option value="beta">测试通道</option></select><button onClick={() => updateAction("configure")} disabled={Boolean(busy)}>切换通道</button><button onClick={() => updateAction("check")} disabled={Boolean(busy)}>检查更新</button></div>
          {(update?.status === "downloading" || update?.progress) ? <div className="progress"><i style={{ width: `${update.progress}%` }} /></div> : null}
          <div className="update-status"><span>{update?.error || update?.message || "日常更新优先下载轻量 Core 包；环境和用户数据保持不变。"}{update?.status === "downloading" ? ` · ${formatBytes(update.speedBps)}/s · ${formatBytes(update.downloadedBytes)}/${formatBytes(update.totalBytes)}` : ""}</span><div>{update?.status === "available" && <button onClick={() => updateAction("download")} disabled={Boolean(busy)}>下载 v{update.latestVersion}</button>}{update?.status === "downloading" && <button onClick={() => updateAction("pause")}>暂停</button>}{update?.status === "paused" && <button onClick={() => updateAction("download")}>继续</button>}{update?.downloaded && <button className="primary" onClick={() => updateAction("install")} disabled={Boolean(busy)}>安装并重启</button>}{["paused", "error"].includes(update?.status || "") && <button onClick={() => updateAction("discard")}>清除下载</button>}{update?.rollbackAvailable && <button onClick={() => updateAction("rollback")} disabled={Boolean(busy)}>回滚上一版</button>}</div></div>
          <div className="tool-actions"><button onClick={() => maintenance("verify", "完整验收")} disabled={Boolean(busy)}><i>✓</i><span>完整验收<small>环境、模型与服务</small></span></button><button onClick={() => maintenance("repair", "依赖修复")} disabled={Boolean(busy)}><i>↻</i><span>依赖修复<small>只补齐未通过项目</small></span></button><button onClick={() => window.launcher.open("logs")}><i>≡</i><span>运行日志<small>查看原始记录</small></span></button><button onClick={() => void exportDiagnostics()} disabled={Boolean(busy)}><i>⇩</i><span>诊断报告<small>脱敏状态与日志</small></span></button></div>
        </div>}
      </section>
    </main>

    <footer><div><span>应用与数据存储目录</span><code>{data?.home || "检测中…"}</code><button disabled={Boolean(busy) || Boolean(runtime.active)} onClick={selectStorage}>{data?.storage?.active ? `迁移中 ${data.storage.progress}%` : "更改存储位置"}</button></div><div><span>环境清单 {runtime.runtimeVersion || "…"} · PowerShell 7 {data?.ps7Ready ? "已就绪" : "待安装"}</span><button onClick={async () => { const result = await window.launcher.shortcut(); setNotice(result.ok ? "桌面快捷方式已创建" : "创建失败"); }}>创建桌面快捷方式</button></div></footer>
    {announcementOpen && <div className="announcement-backdrop">
      <section className="announcement-dialog" role="dialog" aria-modal="true" aria-labelledby="announcement-title">
        <header><div><span className="eyebrow">MINDSPACE RELEASES</span><h2 id="announcement-title">{announcementView === "update" ? "发现新版本" : "版本公告"}</h2></div><button className="announcement-close" aria-label="关闭公告" onClick={() => setAnnouncementOpen(false)}>×</button></header>
        <nav className="announcement-tabs"><button className={announcementView === "update" ? "active" : ""} disabled={!update || update.updateKind === "none"} onClick={() => setAnnouncementView("update")}>本次更新</button><button className={announcementView === "history" ? "active" : ""} onClick={() => setAnnouncementView("history")}>历史公告</button></nav>
        {announcementView === "update" ? <div className="announcement-current">
          <div className="announcement-version"><span>NEW</span><strong>Mindspace {currentAnnouncement.version}</strong><small>{currentAnnouncement.published_at}</small></div>
          <h3>{currentAnnouncement.title}</h3>
          <ul>{currentAnnouncement.summary.map((item) => <li key={item}>{item}</li>)}</ul>
          {(update?.status === "downloading" || update?.progress) && <div className="announcement-progress"><i style={{ width: `${update.progress}%` }} /><span>{update.status === "downloading" ? `${update.progress.toFixed(1)}% · ${formatBytes(update.speedBps)}/s` : update.message}</span></div>}
          <div className="announcement-actions"><button onClick={() => setAnnouncementOpen(false)}>稍后处理</button><button className="primary" disabled={!update || ["downloading", "verifying", "installing"].includes(update.status) || Boolean(busy)} onClick={() => void announcementUpdateAction()}>{update?.downloaded || update?.status === "downloaded" ? update.updateKind === "launcher" ? "静默更新并重启" : "安装并重启服务" : update?.status === "paused" ? "继续下载" : update?.status === "error" ? "重试下载" : "后台下载并校验"}</button></div>
        </div> : <div className="announcement-history">{releaseHistory.map((entry) => <article key={entry.version}><div><strong>v{entry.version}</strong><time>{entry.published_at}</time></div><section><h3>{entry.title}</h3><ul>{entry.summary.map((item) => <li key={item}>{item}</li>)}</ul></section></article>)}</div>}
      </section>
    </div>}
  </div>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
