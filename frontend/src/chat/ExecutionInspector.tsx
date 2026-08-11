import { useCallback, useEffect, useState } from "react";
import { request } from "../shared/api";
import type { InspectorEvent, InspectorTab, PromptInspection, ToolExecution } from "../types";

const asRecord = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const bool = (value: unknown) => Boolean(value);
const num = (value: unknown, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const str = (value: unknown) => String(value ?? "");
function formatTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
const PROMPT_LAYER_LABELS: Record<string, string> = {
  system_and_current_character_profile: "角色与系统规则",
  system_data_contract: "数据契约",
  global_user_and_character_runtime: "权威档案与运行状态",
  conversation_summary: "压缩连续性包",
  retrieval_context: "RAG 召回输入",
  conversation_history: "近期原始历史",
  turn_control: "本轮时间与控制",
  current_user: "当前用户输入",
  roleplay_post_history: "最终角色校准",
};

export function ExecutionInspector({ open, tab, onTab, onClose, events, retrieval, runId }: { open: boolean; tab: InspectorTab; onTab: (tab: InspectorTab) => void; onClose: () => void; events: InspectorEvent[]; retrieval: Record<string, unknown>[]; runId: string }) {
  const [prompt, setPrompt] = useState<PromptInspection | null>(null);
  const [promptError, setPromptError] = useState("");
  const loadPrompt = useCallback(async (reveal = false) => {
    if (!runId) return;
    setPromptError("");
    try {
      setPrompt(await request<PromptInspection>(`/api/v1/runs/${encodeURIComponent(runId)}/prompt-inspection${reveal ? "?reveal=true" : ""}`));
    } catch (error) {
      setPromptError((error as Error).message);
    }
  }, [runId]);
  useEffect(() => {
    if (open && tab === "prompt") void loadPrompt(true);
  }, [loadPrompt, open, tab]);
  const compactCount = prompt?.layers.filter((layer) => layer.layer === "conversation_summary").length || 0;
  const historyCount = prompt?.layers.filter((layer) => layer.layer === "conversation_history").length || 0;
  return <aside className={`inspector ${open ? "open" : ""}`} hidden={!open} aria-hidden={!open}>
    <header><div><span className="eyebrow">LIVE TRACE</span><h2>执行详情</h2><small>节点、RAG 与实际发送给模型的完整输入</small></div><button onClick={onClose} aria-label="关闭执行详情">×</button></header>
    <div className="inspector-tabs">
      <button className={tab === "flow" ? "active" : ""} onClick={() => onTab("flow")}>编排流程</button>
      <button className={tab === "context" ? "active" : ""} onClick={() => onTab("context")}>RAG 引用 <b>{retrieval.length}</b></button>
      <button className={tab === "prompt" ? "active" : ""} onClick={() => onTab("prompt")}>模型实际输入{prompt ? <b>{prompt.message_count}</b> : null}</button>
    </div>
    {tab === "flow" ? <div className="trace-list">{events.length ? events.map((item, index) => <TraceItem item={item} key={`${item.event}-${index}`} />) : <div className="empty-mini">发送消息后，这里会实时显示检索、生成、校验和写回节点。</div>}</div> : tab === "context" ? <div className="context-list"><div className="context-scope-note"><strong>这里只统计 RAG 候选</strong><span>压缩连续性包、最近原始对话、角色档案和本轮控制位于“模型实际输入”，不计入这里的 {retrieval.length} 条。</span></div>{retrieval.length ? retrieval.map((item, index) => <article key={str(item.chunk_id || index)}><header><span>{str(item.source || "召回内容")}</span><b>{num(item.weighted_score || item.score).toFixed(3)}</b></header><p>{str(item.text)}</p><small>{str(asRecord(item.metadata).source || item.session_id || "")}</small></article>) : <div className="empty-mini">本轮没有 RAG 引用；这不代表模型没有收到压缩上下文或近期对话。</div>}</div> : <div className="prompt-inspection">{!runId ? <div className="empty-mini">发送消息后可检查该轮实际模型输入。</div> : promptError ? <div className="empty-mini">{promptError}</div> : !prompt ? <div className="empty-mini">正在读取实际模型输入…</div> : <><header className="prompt-inspection-head"><div><strong>实际发送 {prompt.message_count} 层 · {prompt.total_chars} 字符 · 约 {prompt.estimated_tokens} tokens</strong><small>Run {prompt.run_id}<br />SHA-256 {prompt.sha256}</small></div><button onClick={() => void loadPrompt(!prompt.revealed)}>{prompt.revealed ? "恢复脱敏" : "临时显示完整内容"}</button></header><div className="prompt-metrics"><span>压缩包 <b>{compactCount}</b></span><span>原始历史 <b>{historyCount}</b></span><span>RAG 层 <b>{prompt.layers.filter((layer) => layer.layer === "retrieval_context").length}</b></span><span>当前输入 <b>{prompt.layers.filter((layer) => layer.layer === "current_user").length}</b></span></div><p className="prompt-order-note">以下顺序就是发送给模型的顺序。字符数统计原始内容；脱敏只影响当前界面显示。</p>{prompt.layers.map((layer) => { const compressed = layer.layer === "conversation_summary"; return <details className={compressed ? "compressed-layer" : ""} open={compressed} key={`${layer.index}-${layer.layer}`}><summary><b><i>{String(layer.index + 1).padStart(2, "0")}</i>{PROMPT_LAYER_LABELS[layer.layer] || layer.layer}{compressed ? <em>已参与</em> : null}</b><span>{layer.role} · {layer.chars} 字</span></summary><pre>{layer.content}</pre></details>; })}</>}</div>}
  </aside>;
}

function safeWebUrl(value: unknown) {
  const url = str(value).trim();
  return /^https?:\/\//i.test(url) ? url : "";
}

function TraceItem({ item }: { item: InspectorEvent }) {
  const data = asRecord(item.data);
  const isTool = item.event.startsWith("tool:") || item.event.startsWith("tool.");
  const isModel = item.event === "model.summary" || item.event.startsWith("model.attempt:");
  return <div className={`trace-item ${item.state || "done"}`}><i /><span><strong>{item.label}</strong><small>{formatTime(item.timestamp)}</small>{item.data != null && <details className="trace-details"><summary>{isTool ? "展开工具状态" : isModel ? "展开模型调用" : "展开节点数据"}</summary>{isTool ? <ToolTraceData data={data} /> : isModel ? <ModelTraceData data={data} /> : <pre>{JSON.stringify(item.data, null, 2)}</pre>}</details>}</span></div>;
}

function ModelTraceData({ data }: { data: Record<string, unknown> }) {
  const attempts = Array.isArray(data.provider_attempts) ? data.provider_attempts.map(asRecord) : [];
  const rows = attempts.length ? attempts : data.request_kind ? [data] : [];
  return <div className="tool-trace">
    {data.total_calls != null && <p><b>逻辑模型调用</b><span>{num(data.total_calls)}</span></p>}
    {data.total_http_attempts != null && <p><b>Provider HTTP 请求</b><span>{num(data.total_http_attempts)}</span></p>}
    {rows.map((attempt, index) => <p key={`${str(attempt.request_kind)}-${num(attempt.attempt)}-${index}`}><b>HTTP #{num(attempt.attempt, index + 1)}</b><span>{str(attempt.request_kind)} · {str(attempt.status)} · {num(attempt.elapsed_ms).toFixed(1)} ms{attempt.http_status ? ` · ${num(attempt.http_status)}` : ""}{str(attempt.retry_reason) ? ` · ${str(attempt.retry_reason)}` : ""}{str(attempt.error) ? ` · ${str(attempt.error)}` : ""}</span></p>)}
  </div>;
}

export function ToolCard({ tool }: { tool: ToolExecution }) {
  const statusLabel = { requested: "已请求", reviewing: "审查中", running: "执行中", success: "已完成", denied: "已拒绝", failed: "失败" }[tool.status];
  const toolLabel = tool.tool === "web" ? "联网搜索" : tool.tool === "memory" ? "记忆检索" : "任务处理";
  const active = ["requested", "reviewing", "running"].includes(tool.status);
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setClock(Date.now()), 100);
    return () => window.clearInterval(timer);
  }, [active]);
  const started = tool.started_at ? Date.parse(tool.started_at) : Number.NaN;
  const liveElapsed = active && Number.isFinite(started) ? Math.max(0, clock - started) : num(tool.elapsed_ms);
  const duration = liveElapsed >= 1000 ? `${(liveElapsed / 1000).toFixed(1)}s` : `${Math.round(liveElapsed)}ms`;
  const startedLabel = tool.started_at ? formatTime(tool.started_at) : "等待开始";
  return <details className={`tool-card ${tool.status}`}>
    <summary>
      <span className="tool-card-copy"><span className="tool-card-title"><i aria-hidden="true" /><b>{toolLabel}</b><em>L{tool.level}</em></span><small title={tool.parameter_summary}>{tool.parameter_summary || "准备查询"}</small></span>
      <span className="tool-card-facts"><b>{active ? duration : statusLabel}</b><small>{startedLabel}{!active && tool.elapsed_ms > 0 ? ` · ${duration}` : ""}{!active && tool.source_count > 0 ? ` · ${tool.source_count} 个来源` : ""}</small></span>
    </summary>
    <ToolTraceData data={tool as unknown as Record<string, unknown>} />
  </details>;
}

function ToolTraceData({ data }: { data: Record<string, unknown> }) {
  const payload = asRecord(data.data);
  const sources = Array.isArray(payload.sources) ? payload.sources.map(asRecord) : [];
  return <div className="tool-trace"><p><b>参数</b><span>{str(data.parameter_summary) || "无"}</span></p><p><b>状态</b><span>{str(data.status || (bool(data.allowed) ? "approved" : "reviewed"))}</span></p>{str(data.error || data.reason) && <p className="tool-error"><b>说明</b><span>{str(data.error || data.reason)}</span></p>}{sources.length > 0 && <details><summary>联网来源（{sources.length}）</summary>{sources.map((source, index) => { const url = safeWebUrl(source.url); return <article key={`${url}-${index}`}><strong>{str(source.title || source.source)}</strong>{str(source.content) && <p>{str(source.content)}</p>}{url && <a href={url} target="_blank" rel="noreferrer">打开来源</a>}</article>; })}</details>}{!sources.length && Object.keys(payload).length > 0 && <pre>{JSON.stringify(payload, null, 2)}</pre>}</div>;
}
