import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { request } from "./api";
import type {
  ActivityDefinition,
  ActivitySession,
  CharacterSummary,
  JournalEntry,
  RelationshipMoment,
} from "./types";

const assetPath = (assetId: string) => {
  if (assetId.startsWith("scene-")) {
    return `/assets/archive/scenes/${assetId}.webp`;
  }
  if (assetId.startsWith("state-")) {
    return `/assets/archive/states/${assetId}.webp`;
  }
  if (assetId.startsWith("journal-cover-")) {
    return `/assets/archive/covers/${assetId}.webp`;
  }
  return "/assets/archive/states/state-asset-missing.webp";
};

const JOURNAL_COVERS = [
  "journal-cover-paper",
  "journal-cover-jade",
  "journal-cover-night",
  "journal-cover-spring",
  "journal-cover-summer",
  "journal-cover-autumn",
  "journal-cover-winter",
  "journal-cover-constellation",
];

function ChapterShell({
  title,
  eyebrow,
  character,
  onBack,
  children,
}: {
  title: string;
  eyebrow: string;
  character: CharacterSummary;
  onBack: () => void;
  children: ReactNode;
}) {
  return <main className="chapter-shell">
    <header className="chapter-header">
      <button className="chapter-back" onClick={onBack}>← 返回对话</button>
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{character.display_name} · {character.relationship_label} · 每一项内容都只属于这张角色卡</p>
      </div>
    </header>
    {children}
  </main>;
}

export function JournalPage({
  character,
  sessionId,
  onBack,
  onChanged,
  notify,
}: {
  character: CharacterSummary;
  sessionId: string;
  onBack: () => void;
  onChanged?: () => Promise<unknown> | void;
  notify: (message: string) => void;
}) {
  const [items, setItems] = useState<JournalEntry[]>([]);
  const [selected, setSelected] = useState<JournalEntry | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const result = await request<{ items: JournalEntry[] }>(
      `/api/v1/characters/${encodeURIComponent(character.character_id)}/journal`,
    );
    setItems(result.items);
    setSelected((current) => result.items.find((item) => item.entry_id === current?.entry_id) || result.items[0] || null);
  }, [character.character_id]);

  useEffect(() => { void load(); }, [load]);

  const generate = async () => {
    setBusy(true);
    try {
      const result = await request<{ entry: JournalEntry; generation: string }>(
        `/api/v1/characters/${encodeURIComponent(character.character_id)}/journal/generate`,
        { method: "POST", body: JSON.stringify({ session_id: sessionId }) },
      );
      await load();
      await onChanged?.();
      setSelected(result.entry);
      notify(result.generation === "llm" ? "日记草稿已生成，确认保存前仍可编辑" : "模型不可用，已生成本地可编辑草稿");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!selected) return;
    const updated = await request<JournalEntry>(
      `/api/v1/characters/${encodeURIComponent(character.character_id)}/journal/${encodeURIComponent(selected.entry_id)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          expected_revision: selected.revision,
          title: selected.title,
          content: selected.content,
          status: "saved",
          cover_asset_id: selected.cover_asset_id,
        }),
      },
    );
    setSelected(updated);
    await load();
    await onChanged?.();
    notify("日记已保存；它仍是角色主观叙事，不会改写人物档案");
  };

  const remove = async (entry: JournalEntry) => {
    if (!window.confirm(`删除「${entry.title}」？`)) return;
    await request(
      `/api/v1/characters/${encodeURIComponent(character.character_id)}/journal/${encodeURIComponent(entry.entry_id)}`,
      { method: "DELETE" },
    );
    await load();
    await onChanged?.();
  };

  return <ChapterShell title="角色日记" eyebrow="SHARED CHAPTERS · JOURNAL" character={character} onBack={onBack}>
    <div className="chapter-toolbar">
      <p>日记只在你点击时生成，最多调用模型一次；失败时仍会留下本地草稿。</p>
      <button className="chapter-primary" disabled={busy} onClick={() => void generate()}>
        {busy ? "正在整理…" : "根据最近对话生成草稿"}
      </button>
    </div>
    <div className="journal-layout">
      <aside className="journal-index">
        {items.length ? items.map((item) => <button
          key={item.entry_id}
          className={selected?.entry_id === item.entry_id ? "active" : ""}
          onClick={() => setSelected(item)}
        >
          <img src={assetPath(item.cover_asset_id)} alt="" loading="lazy" />
          <strong>{item.title}</strong>
          <small>{item.status === "saved" ? "已保存" : "草稿"} · {new Date(item.updated_at).toLocaleDateString()}</small>
        </button>) : <div className="chapter-empty">
          <img src="/assets/archive/states/state-journal-empty.webp" alt="" />
          <p>还没有留下日记。</p>
        </div>}
      </aside>
      <section
        className="journal-editor"
        style={{
          backgroundImage: [
            'url("/assets/archive/frames/frame-journal-paper.svg")',
            'url("/assets/archive/texture-paper.webp")',
          ].join(", "),
        }}
      >
        {selected ? <>
          <div className="journal-cover-picker" aria-label="日记封面">
            {JOURNAL_COVERS.map((coverId) => <button
              key={coverId}
              className={selected.cover_asset_id === coverId ? "active" : ""}
              onClick={() => setSelected({ ...selected, cover_asset_id: coverId })}
              title={coverId}
            >
              <img src={assetPath(coverId)} alt="" loading="lazy" />
            </button>)}
          </div>
          <input aria-label="日记标题" value={selected.title} onChange={(event) => setSelected({ ...selected, title: event.target.value })} />
          <textarea aria-label="日记正文" value={selected.content} onChange={(event) => setSelected({ ...selected, content: event.target.value })} />
          <footer>
            <span>主观叙事 · 不作为人物档案证据</span>
            <button className="danger-text" onClick={() => void remove(selected)}>删除</button>
            <button className="chapter-primary" onClick={() => void save()}>保存日记</button>
          </footer>
        </> : <div className="chapter-empty wide"><p>选择一篇日记，或生成第一份草稿。</p></div>}
      </section>
    </div>
  </ChapterShell>;
}

export function MomentsPage({
  character,
  onBack,
  onChanged,
  notify,
}: {
  character: CharacterSummary;
  onBack: () => void;
  onChanged?: () => Promise<unknown> | void;
  notify: (message: string) => void;
}) {
  const [items, setItems] = useState<RelationshipMoment[]>([]);
  const load = useCallback(async () => {
    const result = await request<{ items: RelationshipMoment[] }>(
      `/api/v1/characters/${encodeURIComponent(character.character_id)}/moments`,
    );
    setItems(result.items);
  }, [character.character_id]);
  useEffect(() => { void load(); }, [load]);

  const update = async (item: RelationshipMoment, status: "saved" | "archived") => {
    await request(
      `/api/v1/characters/${encodeURIComponent(character.character_id)}/moments/${encodeURIComponent(item.moment_id)}`,
      { method: "PUT", body: JSON.stringify({ expected_revision: item.revision, status }) },
    );
    await load();
    await onChanged?.();
    notify(status === "saved" ? "共同片段已收藏" : "共同片段已移出时间线");
  };

  return <ChapterShell title="共同片段" eyebrow="SHARED CHAPTERS · MOMENTS" character={character} onBack={onBack}>
    <section className="moment-timeline">
      {items.length ? items.map((item) => <article key={item.moment_id} className={`moment-card ${item.status}`}>
        <span className="moment-dot" />
        <div>
          <small>{new Date(item.created_at).toLocaleString()} · {item.status === "candidate" ? "待确认" : "已收藏"}</small>
          <h2>{item.title}</h2>
          <p>{item.summary}</p>
          <footer>
            {item.status === "candidate" && <button className="chapter-primary" onClick={() => void update(item, "saved")}>确认收藏</button>}
            <button onClick={() => void update(item, "archived")}>移出时间线</button>
          </footer>
        </div>
      </article>) : <div className="chapter-empty wide">
        <img src="/assets/archive/states/state-moment-empty.webp" alt="" />
        <img className="chapter-empty-sticker" src="/assets/archive/stickers/sticker-wax-seal.svg" alt="" />
        <p>活动完成后会形成候选片段，只有确认后才进入这里。</p>
      </div>}
    </section>
  </ChapterShell>;
}

export function ActivitiesPage({
  character,
  sessionId,
  onBack,
  onContinueChat,
  onChanged,
  notify,
}: {
  character: CharacterSummary;
  sessionId: string;
  onBack: () => void;
  onContinueChat: (activitySessionId: string) => void;
  onChanged?: () => Promise<unknown> | void;
  notify: (message: string) => void;
}) {
  const [definitions, setDefinitions] = useState<ActivityDefinition[]>([]);
  const [sessions, setSessions] = useState<ActivitySession[]>([]);
  const [current, setCurrent] = useState<ActivitySession | null>(null);
  const [answer, setAnswer] = useState("");

  useEffect(() => {
    void request<{ items: ActivityDefinition[] }>("/api/v1/activities").then((result) => setDefinitions(result.items));
    void request<{ items: ActivitySession[] }>(
      `/api/v1/characters/${encodeURIComponent(character.character_id)}/activity-sessions`,
    ).then((result) => setSessions(result.items));
  }, [character.character_id]);

  const definition = useMemo(
    () => definitions.find((item) => item.activity_id === current?.activity_id),
    [current?.activity_id, definitions],
  );
  const canComplete = Boolean(current && (
    (current.activity_id === "scene_companion" && current.phase === "conversation")
    || (current.activity_id === "mutual_questions"
      && Array.isArray(current.state.answers)
      && current.state.answers.length > 0)
    || (current.activity_id === "story_choices" && current.phase === "node:ending")
  ));

  const start = async (item: ActivityDefinition) => {
    const started = await request<ActivitySession>(
      `/api/v1/activities/${encodeURIComponent(item.activity_id)}/sessions`,
      { method: "POST", body: JSON.stringify({ character_id: character.character_id, session_id: sessionId }) },
    );
    setCurrent(started);
    setSessions((items) => [started, ...items]);
  };

  const act = async (action: string, payload: Record<string, unknown> = {}) => {
    if (!current) return;
    const result = await request<{ session: ActivitySession; result: Record<string, unknown>; idempotent_replay: boolean }>(
      `/api/v1/activity-sessions/${encodeURIComponent(current.activity_session_id)}/actions`,
      {
        method: "POST",
        body: JSON.stringify({
          action_id: crypto.randomUUID(),
          expected_revision: current.revision,
          action,
          payload,
        }),
      },
    );
    setCurrent(result.session);
    setSessions((items) => [result.session, ...items.filter((item) => item.activity_session_id !== result.session.activity_session_id)]);
    if (action === "answer_question") setAnswer("");
    if (result.result.candidate_moment) notify("活动已完成，候选共同片段等待你的确认");
    if (result.result.candidate_moment) await onChanged?.();
  };

  return <ChapterShell title="陪伴活动" eyebrow="SHARED CHAPTERS · ACTIVITIES" character={character} onBack={onBack}>
    {!current ? <>
      <section className="activity-grid">
        {definitions.map((item) => <article key={item.activity_id}>
          <img src={assetPath(item.cover_asset_id)} alt="" loading="lazy" />
          <div><h2>{item.title}</h2><p>{item.description}</p><button className="chapter-primary" onClick={() => void start(item)}>开始活动</button></div>
        </article>)}
      </section>
      {sessions.length > 0 && <section className="activity-history">
        <h2>最近活动</h2>
        {sessions.slice(0, 8).map((item) => <button key={item.activity_session_id} onClick={() => setCurrent(item)}>
          <strong>{definitions.find((definitionItem) => definitionItem.activity_id === item.activity_id)?.title || item.activity_id}</strong>
          <span>{item.status === "active" ? "进行中" : item.status === "interrupted" ? "已中断 · 可恢复" : "已完成"}</span>
        </button>)}
      </section>}
    </> : <section className="activity-stage">
      <header>
        <div><span className="eyebrow">当前活动</span><h2>{definition?.title}</h2><p>{definition?.description}</p></div>
        <button onClick={() => setCurrent(null)}>返回活动列表</button>
      </header>
      {current.activity_id === "scene_companion" && current.phase === "choose_scene" && <div className="scene-grid">
        {definition?.scenes?.map((scene) => <button key={scene.scene_id} onClick={() => void act("select_scene", { scene_id: scene.scene_id })}>
          <img src={assetPath(scene.asset_id)} alt="" loading="lazy" /><strong>{scene.title}</strong><span>{scene.description}</span>
        </button>)}
      </div>}
      {current.activity_id === "scene_companion" && current.phase === "conversation" && <div className="activity-action-panel">
        <p>场景已锁定。继续对话时，服务端会把当前活动作为临时 System 层提供给角色。</p>
        <button className="chapter-primary" onClick={() => onContinueChat(current.activity_session_id)}>进入持续对话</button>
      </div>}
      {current.activity_id === "mutual_questions" && <div className="activity-action-panel">
        {typeof current.state.current_question === "string"
          ? <><blockquote>{current.state.current_question}</blockquote><textarea value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="写下你的回答" /><button className="chapter-primary" disabled={!answer.trim()} onClick={() => void act("answer_question", { answer })}>提交回答</button></>
          : <button className="chapter-primary" onClick={() => void act("draw_question")}>抽取一个问题</button>}
        <button onClick={() => onContinueChat(current.activity_session_id)}>带着当前问题去对话</button>
      </div>}
      {current.activity_id === "story_choices" && <div className="activity-action-panel">
        <blockquote>{definition?.nodes?.[current.phase.replace("node:", "")]?.text}</blockquote>
        <div className="story-choices">{definition?.nodes?.[current.phase.replace("node:", "")]?.choices.map((choice) =>
          <button key={choice.choice_id} onClick={() => void act("choose_story", { choice_id: choice.choice_id })}>{choice.label}</button>
        )}</div>
        <button onClick={() => onContinueChat(current.activity_session_id)}>让角色回应此刻</button>
      </div>}
      {current.status === "active" && <footer className="activity-stage-footer">
        <button onClick={() => void act("cancel")}>中断并保留现场</button>
        <button className="chapter-primary" disabled={!canComplete} onClick={() => void act("complete")}>完成并生成候选片段</button>
      </footer>}
      {current.status === "interrupted" && <div className="activity-action-panel">
        <p>现场已保存，没有调用模型，也没有自动推进。</p>
        <button className="chapter-primary" onClick={() => void act("resume")}>从中断处恢复</button>
      </div>}
      {current.status === "completed" && <div className="chapter-empty wide"><p>活动已完成。候选片段不会自动收藏，请到共同片段中确认。</p></div>}
    </section>}
  </ChapterShell>;
}
