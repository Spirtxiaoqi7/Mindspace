import { useCallback, useEffect, useState } from "react";

import { Modal } from "../../shared/Modal";
import { Field } from "../../shared/Field";
import { request } from "../../shared/api";
import { formatTime, str } from "../../shared/formatters";
import type { KnowledgeItem } from "../../types";
import { styledConfirm } from "../../ui/styledConfirm";
export function KnowledgeDialog({ onClose, onDirty, notify }: { onClose: () => void; onDirty: (dirty: boolean) => void; notify: (message: string) => void }) {
  const [items, setItems] = useState<KnowledgeItem[]>([]); const [query, setQuery] = useState(""); const [text, setText] = useState(""); const [source, setSource] = useState("手动录入"); const [loading, setLoading] = useState(false); const [deletingId, setDeletingId] = useState("");
  const load = useCallback(async () => { setLoading(true); try { const result = await request<{ items: KnowledgeItem[] }>(`/api/v1/knowledge?query=${encodeURIComponent(query)}`); setItems(result.items); } catch (error) { notify((error as Error).message); } finally { setLoading(false); } }, [notify, query]);
  useEffect(() => { void load(); }, [load]); useEffect(() => { onDirty(Boolean(text.trim())); return () => onDirty(false); }, [onDirty, text]);
  const add = async () => { try { const result = await request<{ count: number }>("/api/v1/knowledge", { method: "POST", body: JSON.stringify({ text, source }) }); setText(""); notify(`已写入 ${result.count} 个知识块`); await load(); } catch (error) { notify((error as Error).message); } };
  const upload = async (file: File) => { const form = new FormData(); form.append("file", file); try { const result = await request<{ count: number }>("/api/v1/knowledge/upload", { method: "POST", body: form }); notify(`已从 ${file.name} 导入 ${result.count} 个知识块`); await load(); } catch (error) { notify((error as Error).message); } };
  const remove = async (item: KnowledgeItem) => {
    if (deletingId) return;
    if (!(await styledConfirm({ title: "删除这个知识块？", message: "删除后，该内容不会再参与知识检索。", confirmLabel: "删除知识", danger: true }))) return;
    setDeletingId(item.chunk_id);
    try { await request(`/api/v1/knowledge/${item.chunk_id}`, { method: "DELETE" }); notify("知识块已删除"); await load(); }
    catch (error) { notify(`删除知识块失败：${(error as Error).message}`); }
    finally { setDeletingId(""); }
  };
  return <Modal title="全局知识库" kicker="KNOWLEDGE BASE" onClose={onClose}><div className="knowledge-layout"><section className="knowledge-compose"><h3>新增资料</h3><Field label="来源名称" value={source} onChange={(next) => setSource(str(next))} /><Field label="知识内容" value={text} type="textarea" onChange={(next) => setText(str(next))} placeholder="粘贴文本，空行会成为自然分块边界" /><div className="row-actions"><label className="upload-button">上传 TXT / MD / JSON<input hidden type="file" accept=".txt,.md,.json" onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); event.currentTarget.value = ""; }} /></label><button className="primary" disabled={!text.trim()} onClick={() => void add()}>保存知识</button></div></section><section className="knowledge-manage"><div className="manage-head"><h3>知识块 <b>{items.length}</b></h3><label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索内容或来源" /></label></div>{loading ? <div className="empty-mini">正在读取知识库…</div> : <div className="knowledge-list">{items.length ? items.map((item) => <article key={item.chunk_id}><header><span>{item.source}</span><button disabled={Boolean(deletingId)} onClick={() => void remove(item)}>{deletingId === item.chunk_id ? "正在删除…" : "删除"}</button></header><p>{item.text}</p><small>{item.chunk_id} · {formatTime(item.created_at)}</small></article>) : <div className="empty-mini">知识库中暂无匹配内容</div>}</div>}</section></div></Modal>;
}
