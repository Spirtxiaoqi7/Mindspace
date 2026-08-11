import { useCallback, useEffect, useState } from "react";

import { Modal } from "../../shared/Modal";
import { request } from "../../shared/api";
import { formatTime, friendlyValue } from "../../shared/formatters";
import type { EventMemoryItem, EventMemorySnapshot, MemoryItem } from "../../types";
import { styledConfirm } from "../../ui/styledConfirm";
export function MemoryDialog({ characterId, onClose, onDirty, notify }: { characterId: string; onClose: () => void; onDirty: (dirty: boolean) => void; notify: (message: string) => void }) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [events, setEvents] = useState<EventMemorySnapshot>({
    schema_version: "1.0.0", character_id: characterId, revision: 0, pending: [],
    subjects: { user_related: null, ai_related: null, relationship_related: null },
    history: [], updated_at: "",
  });
  const [includeHistory, setIncludeHistory] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [editingKey, setEditingKey] = useState("");
  const [draft, setDraft] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [memoryResult, eventResult] = await Promise.all([
        request<{ items: MemoryItem[] }>(`/api/v1/memory/items?include_history=${includeHistory ? "true" : "false"}&character_id=${encodeURIComponent(characterId)}`),
        request<EventMemorySnapshot>(`/api/v1/memory/events?character_id=${encodeURIComponent(characterId)}`),
      ]);
      setItems(memoryResult.items);
      setEvents(eventResult);
    } catch (error) { notify((error as Error).message); }
    finally { setLoading(false); }
  }, [characterId, includeHistory, notify]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { onDirty(Boolean(editingKey)); return () => onDirty(false); }, [editingKey, onDirty]);
  const filtered = items.filter((item) => !query.trim() || `${item.category} ${item.display_name} ${item.value} ${item.source_text || ""}`.toLowerCase().includes(query.trim().toLowerCase()));
  const save = async (item: MemoryItem) => {
    try {
      await request(`/api/v1/memory/items/${encodeURIComponent(item.memory_key)}`, { method: "PUT", body: JSON.stringify({ value: draft }) });
      setEditingKey(""); setDraft(""); notify("记忆已更新，并同步到权威档案"); await load();
    } catch (error) { notify((error as Error).message); }
  };
  const remove = async (item: MemoryItem) => {
    if (!(await styledConfirm({ title: `删除“${item.display_name}”？`, message: String(item.value), detail: "权威档案会同步更新，这条内容也会退出后续召回。", confirmLabel: "删除记忆", danger: true }))) return;
    try { await request(`/api/v1/memory/items/${encodeURIComponent(item.memory_key)}`, { method: "DELETE" }); notify("记忆已删除并退出召回"); await load(); }
    catch (error) { notify((error as Error).message); }
  };
  const restore = async (item: MemoryItem) => {
    try { await request("/api/v1/memory/restore", { method: "POST", body: JSON.stringify({ memory_key: item.memory_key }) }); notify("记忆已恢复并同步到权威档案"); await load(); }
    catch (error) { notify((error as Error).message); }
  };
  const completeEvent = async (item: EventMemoryItem) => {
    try { await request(`/api/v1/memory/events/${encodeURIComponent(item.id)}/complete?character_id=${encodeURIComponent(characterId)}`, { method: "POST" }); notify(`已完成：${item.title}`); await load(); }
    catch (error) { notify((error as Error).message); }
  };
  const removeEvent = async (item: EventMemoryItem) => {
    if (!(await styledConfirm({ title: `删除事件“${item.title}”？`, message: item.summary, detail: "删除后不再参与中期上下文，但不会影响长期 RAG。", confirmLabel: "删除事件", danger: true }))) return;
    try { await request(`/api/v1/memory/events/${encodeURIComponent(item.id)}?character_id=${encodeURIComponent(characterId)}`, { method: "DELETE" }); notify("事件记忆已删除"); await load(); }
    catch (error) { notify((error as Error).message); }
  };
  const categoryLabels = { user_related: "用户相关", ai_related: "AI 相关", relationship_related: "关系相关" } as const;
  const renderEvent = (item: EventMemoryItem | null, label: string, index: number, completable = false) => <article className={`event-memory-card${item ? " filled" : " empty"}`} key={`${label}-${item?.id || index}`}><header><span>{label}</span>{item?.due_at && <time>{new Date(item.due_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>}</header>{item ? <><strong>{item.title}</strong><p>{item.summary}</p><footer>{completable && <button onClick={() => void completeEvent(item)}>完成</button>}<button className="danger-text" onClick={() => void removeEvent(item)}>删除</button></footer></> : <p>空槽位</p>}</article>;
  const pendingSlots = Array.from({ length: 3 }, (_, index) => events.pending[index] || null);
  return <Modal title="记忆中心" kicker="MEMORY CENTER" onClose={onClose}><section className="event-memory-panel"><header className="event-memory-heading"><div><span>EVENT LEDGER</span><h3>事件记忆</h3><p>承接近期事项与重要变化，最多六条；不进入长期 RAG。</p></div><button onClick={() => void load()}>刷新</button></header>{loading ? <div className="empty-mini">正在读取事件记忆…</div> : <div className="event-memory-lanes"><section><header><strong>近期 / 待办</strong><small>{events.pending.length} / 3</small></header><div>{pendingSlots.map((item, index) => renderEvent(item, `待办 ${index + 1}`, index, true))}</div></section><section><header><strong>主体事件</strong><small>{Object.values(events.subjects).filter(Boolean).length} / 3</small></header><div>{(["user_related", "ai_related", "relationship_related"] as const).map((category, index) => renderEvent(events.subjects[category], categoryLabels[category], index))}</div></section></div>}</section><div className="memory-toolbar"><label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索长期记忆" /></label><label className="memory-history-toggle"><input type="checkbox" checked={includeHistory} onChange={(event) => setIncludeHistory(event.target.checked)} />显示已失效记忆</label></div><p className="advanced-note">下方是长期结构化记忆。修改、删除和恢复会同步权威档案；事件区与长期 RAG 相互独立。</p>{loading ? <div className="empty-mini">正在读取长期记忆…</div> : <div className="memory-list">{filtered.length ? filtered.map((item) => <article className={item.status === "invalidated" ? "invalidated" : ""} key={`${item.status}-${item.memory_key}-${item.invalidated_at || ""}`}><header><div><span>{item.category}</span><strong>{item.display_name}</strong></div><small>{item.status === "active" ? "当前有效" : "已失效"} · {formatTime(item.updated_at || item.invalidated_at)}</small></header>{editingKey === item.memory_key && item.status === "active" ? <div className="memory-edit"><input autoFocus value={draft} onChange={(event) => setDraft(event.target.value)} /><button className="secondary" onClick={() => { setEditingKey(""); setDraft(""); }}>取消</button><button className="primary" disabled={!draft.trim()} onClick={() => void save(item)}>保存</button></div> : <p className="memory-value">{friendlyValue(item.value)}</p>}<details><summary>为什么记住</summary><p>{item.source_text || "来自用户在记忆中心的明确操作"}</p>{item.session_id && <small>来源会话：{item.session_id}</small>}</details><footer>{item.status === "active" ? <><button onClick={() => { setEditingKey(item.memory_key); setDraft(String(item.value)); }}>修改</button><button className="danger-text" onClick={() => void remove(item)}>删除</button></> : <button onClick={() => void restore(item)}>恢复这条记忆</button>}</footer></article>) : <div className="empty-mini">暂无匹配的长期结构化记忆。</div>}</div>}</Modal>;
}
