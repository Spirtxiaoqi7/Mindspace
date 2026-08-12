import { useEffect, useRef, useState } from "react";
import { request } from "../shared/api";
import type { CharacterCardV2, CharacterRecord, CharacterSummary } from "../types";

export type AppView = "modes" | "draw" | "characters" | "chat" | "scenes";

function avatar(character: CharacterSummary | CharacterRecord) {
  return character.avatar?.src || "/assets/avatar-ai-default.webp";
}

function fallbackAvatar(event: React.SyntheticEvent<HTMLImageElement>) {
  const image = event.currentTarget;
  if (!image.src.endsWith("/assets/avatar-ai-default.webp")) image.src = "/assets/avatar-ai-default.webp";
}

function cardFor(record: CharacterRecord): CharacterCardV2 {
  return record.card || { spec: "chara_card_v2", spec_version: "2.0", data: { name: record.display_name, description: "", personality: "", scenario: record.relationship_label || "", first_mes: "", mes_example: "", alternate_greetings: [], tags: [], creator: "Mindspace", character_version: "1.0", extensions: { mindspace: { gender: record.gender } } } };
}

export function ModeLobby({
  characters, userName, interrupted, onDraw, onCustom: _onCustom, onLibrary, onResume,
}: {
  characters: CharacterSummary[]; userName: string; interrupted?: { session_id: string; title: string };
  onDraw: () => void; onCustom: () => void; onLibrary: () => void; onResume: (sessionId: string) => void;
}) {
  const primary = [...characters].sort((a, b) => b.last_used_at.localeCompare(a.last_used_at))[0];
  return <main className="mode-lobby">
    <header className="mode-lobby-head"><div><span className="eyebrow">MINDSPACE</span><h1>{primary ? `${userName || "你"} × ${primary.display_name}` : "让一个角色从命格中诞生"}</h1></div><button className="archive-link" onClick={onLibrary}>角色库 <b>{characters.length}</b></button></header>
    {interrupted && <button className="resume-session" onClick={() => onResume(interrupted.session_id)}>继续上次对话：{interrupted.title}</button>}
    <section className="mode-card-grid">
      <button className="mode-card draw-card" onClick={onDraw}><div className="mode-card-ornament" aria-hidden="true">✦</div><span>FATE SYSTEM · 十二命格</span><h2>命定系统</h2><p>先看见八个彼此不同的人，再从十二宫选择真正属于她的部分，最终生成一张 V2 角色卡。</p><footer><b>进入命格</b><small>{characters.some((item) => item.source === "draw") ? "继续创造另一位角色" : "约 3 分钟完成首次命定"}</small></footer></button>
      <button className="mode-card continue-card" onClick={onLibrary}><span>CHARACTER LIBRARY</span><h2>人物与记忆</h2><p>选择已有角色，管理其基础角色卡、偏好与任务，然后继续对话。</p><footer><b>打开角色库</b></footer></button>
    </section>
    <div className="mode-lobby-footnote"><span>V2 角色卡</span><span>多角色隔离</span><span>可导入导出</span><span>偏好与任务记忆</span></div>
  </main>;
}

export function CharacterPicker({ open, characters, title = "选择本次对话的角色", onClose, onChoose, onDraw }: {
  open: boolean; characters: CharacterSummary[]; title?: string; onClose: () => void;
  onChoose: (character: CharacterSummary) => void; onDraw: () => void;
}) {
  if (!open) return null;
  return <div className="modal-backdrop character-picker-backdrop"><section className="character-picker" role="dialog" aria-modal="true" aria-label={title}>
    <header><div><span className="eyebrow">CHARACTER</span><h2>{title}</h2><p>角色卡、偏好和任务记忆彼此隔离。</p></div><button onClick={onClose}>关闭</button></header>
    <div className="character-picker-grid">{characters.map((character) => <button key={character.character_id} onClick={() => onChoose(character)}><img src={avatar(character)} onError={fallbackAvatar} alt="" /><span><strong>{character.display_name}</strong><small>{character.relationship_label || "V2 角色卡"}</small></span><b>{character.latest_session_id ? "继续" : "开始"}</b></button>)}
      <button className="character-picker-new" onClick={onDraw}><i>＋</i><span><strong>创建新角色</strong><small>前往命定系统</small></span></button>
    </div>
  </section></div>;
}

function EditableCard({ record, onSaved, onError }: { record: CharacterRecord; onSaved: (next: CharacterRecord) => void; onError: (message: string) => void }) {
  const [card, setCard] = useState<CharacterCardV2>(record.card);
  const [busy, setBusy] = useState(false);
  useEffect(() => setCard(record.card), [record]);
  const update = (key: keyof CharacterCardV2["data"], value: string) => setCard((current) => ({ ...current, data: { ...current.data, [key]: value } }));
  const save = async () => { setBusy(true); onError(""); try { const result = await request<{ character: CharacterRecord }>(`/api/v1/characters/${encodeURIComponent(record.character_id)}`, { method: "PUT", body: JSON.stringify({ revision: record.revision, card }) }); onSaved(result.character); } catch (reason) { onError((reason as Error).message); } finally { setBusy(false); } };
  return <section className="v2-card-editor"><header><span className="eyebrow">CHARACTER CARD V2</span><h3>基础角色资料</h3><p>角色的长期设定只保留这些 V2 字段；偏好和任务在记忆中心维护。</p></header>
    <label>名称<input value={card.data.name} maxLength={80} onChange={(event) => update("name", event.target.value)} /></label>
    <label>角色描述<textarea value={card.data.description} onChange={(event) => update("description", event.target.value)} /></label>
    <label>人格<textarea value={card.data.personality} onChange={(event) => update("personality", event.target.value)} /></label>
    <label>基础场景<textarea value={card.data.scenario} onChange={(event) => update("scenario", event.target.value)} /></label>
    <label>首次开场<textarea value={card.data.first_mes} onChange={(event) => update("first_mes", event.target.value)} /></label>
    <label>对话示例<textarea value={card.data.mes_example} onChange={(event) => update("mes_example", event.target.value)} /></label>
    <button className="primary" disabled={busy || !card.data.name.trim()} onClick={() => void save()}>{busy ? "正在保存…" : "保存角色卡"}</button>
  </section>;
}

export function CharacterLibrary({ characters, initialCharacterId, onBack, onRefresh, onChat, onNewChat, onDraw }: {
  characters: CharacterSummary[]; initialCharacterId?: string; onBack: () => void; onRefresh: () => Promise<void>;
  onChat: (character: CharacterSummary) => void; onNewChat: (character: CharacterSummary) => void; onDraw: () => void;
}) {
  const [selectedId, setSelectedId] = useState(initialCharacterId || characters[0]?.character_id || "");
  const [record, setRecord] = useState<CharacterRecord | null>(null);
  const [error, setError] = useState("");
  const [removeArmed, setRemoveArmed] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const importRef = useRef<HTMLInputElement>(null);
  const selectionRequest = useRef(0);
  useEffect(() => {
    const requestId = ++selectionRequest.current;
    if (!selectedId) { setRecord(null); return; }
    setRecord(null); setError(""); setRemoveArmed(false);
    request<CharacterRecord>(`/api/v1/characters/${encodeURIComponent(selectedId)}`)
      .then((next) => { if (selectionRequest.current === requestId) setRecord(next); })
      .catch((reason: Error) => { if (selectionRequest.current === requestId) setError(reason.message); });
  }, [selectedId]);
  useEffect(() => {
    if (!characters.length) { if (selectedId) setSelectedId(""); return; }
    if (!selectedId || !characters.some((item) => item.character_id === selectedId)) setSelectedId(characters[0].character_id);
  }, [characters, selectedId]);
  const importCard = async (file?: File) => { if (!file) return; const form = new FormData(); form.append("file", file); try { const result = await request<{ character: CharacterRecord }>("/api/v1/characters/import", { method: "POST", body: form }); await onRefresh(); setSelectedId(result.character.character_id); } catch (reason) { setError((reason as Error).message); } };
  const removeCharacter = async () => {
    if (!record) return;
    if (!removeArmed) { setRemoveArmed(true); window.setTimeout(() => setRemoveArmed(false), 3000); return; }
    setActionBusy(true); setError("");
    try {
      await request(`/api/v1/characters/${encodeURIComponent(record.character_id)}/archive`, { method: "POST" });
      const next = characters.find((item) => item.character_id !== record.character_id);
      setRecord(null); setSelectedId(next?.character_id || ""); await onRefresh();
    } catch (reason) { setError((reason as Error).message); } finally { setActionBusy(false); setRemoveArmed(false); }
  };
  const selectedSummary = characters.find((item) => item.character_id === selectedId);
  return <main className="character-library"><header><button onClick={onBack}>← 返回主页</button><div><span className="eyebrow">CHARACTER LIBRARY</span><h1>角色库</h1><p>V2 角色卡，以及可变化的偏好与任务。</p></div><div><button onClick={() => importRef.current?.click()}>导入 V2 卡</button><button className="primary" onClick={onDraw}>＋ 进入命定系统</button><input ref={importRef} hidden type="file" accept="application/json,.json" onChange={(event) => { void importCard(event.target.files?.[0]); event.currentTarget.value = ""; }} /></div></header>
    <div className="library-layout"><nav className="card-shelf" aria-label="角色列表">{characters.map((character) => <button className={character.character_id === selectedId ? "active" : ""} key={character.character_id} onClick={() => setSelectedId(character.character_id)}><img src={avatar(character)} onError={fallbackAvatar} alt="" /><span><strong>{character.display_name}</strong><small>{character.source === "draw" ? "命定角色" : "导入角色"}</small></span><i>{character.character_id === selectedId ? "查看中" : "查看"}</i></button>)}</nav>
      {record ? <section className="library-detail"><div className="library-hero"><img src={avatar(record)} onError={fallbackAvatar} alt="" /><div><span>{record.gender} · V2</span><h2>{cardFor(record).data.name}</h2><p>{cardFor(record).data.description}</p><div className="library-primary-actions">{selectedSummary?.latest_session_id && <button onClick={() => onChat(record)}>继续对话</button>}<button className="primary" onClick={() => onNewChat(record)}>{selectedSummary?.latest_session_id ? "新建会话" : "开始对话"}</button></div></div></div>
        <div className="library-actions"><a href={`/api/v1/characters/${encodeURIComponent(record.character_id)}/export`} download>导出 V2 JSON</a><button className={removeArmed ? "danger armed" : "danger"} disabled={actionBusy} onClick={() => void removeCharacter()}>{actionBusy ? "正在移出…" : removeArmed ? "再次点击确认移出" : "移出角色库"}</button></div>
        {error && <p className="library-error" role="alert">{error}</p>}
        <EditableCard record={{ ...record, card: cardFor(record) }} onError={setError} onSaved={(next) => { setRecord(next); void onRefresh(); }} />
      </section> : <div className="library-empty"><b>还没有角色</b><p>从命格系统开始，或导入 SillyTavern V2 JSON 角色卡。</p><button onClick={onDraw}>开始命定</button></div>}
    </div>
  </main>;
}
