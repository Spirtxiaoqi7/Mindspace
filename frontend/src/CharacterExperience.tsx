import { useEffect, useMemo, useRef, useState } from "react";
import { request } from "./api";
import type {
  CharacterDraft,
  CharacterRecord,
  CharacterSummary,
} from "./types";

type AppView =
  | "modes"
  | "draw"
  | "characters"
  | "chat"
  | "journal"
  | "moments"
  | "scenes"
  | "activities";

interface CharacterOption {
  id: string;
  label: string;
  conflicts?: string[];
}

interface CharacterOptions {
  core_traits: CharacterOption[];
  flaws: CharacterOption[];
  relationships: string[];
  gender: Array<"男" | "女">;
}

interface CharacterInput {
  ai_name: string;
  ai_gender: "男" | "女";
  core_traits: string[];
  flaw: string;
  relationship: string;
  user_name: string;
  user_alias: string;
}

const DEFAULT_INPUT: CharacterInput = {
  ai_name: "",
  ai_gender: "女",
  core_traits: [],
  flaw: "",
  relationship: "朋友",
  user_name: "",
  user_alias: "",
};

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};

const text = (value: unknown) => typeof value === "string" ? value : "";

function characterAvatar(character: CharacterSummary | CharacterRecord) {
  return character.avatar?.src || "/assets/avatar-ai-default.webp";
}

export function ModeLobby({
  characters,
  interrupted,
  onDraw,
  onCustom,
  onLibrary,
  onResume,
}: {
  characters: CharacterSummary[];
  interrupted?: { session_id: string; title: string };
  onDraw: () => void;
  onCustom: () => void;
  onLibrary: () => void;
  onResume: (sessionId: string) => void;
}) {
  const drawCount = characters.filter((item) => item.source === "draw").length;
  return <main className="mode-lobby">
    <header className="mode-lobby-header">
      <div className="brand-mark">M</div>
      <div>
        <span className="eyebrow">MINDSPACE · CHARACTER ARCHIVE</span>
        <h1>今天，想和谁见面？</h1>
        <p>选择一种开始方式。角色、关系、记忆和会话会彼此隔离，不会串到另一张卡里。</p>
      </div>
      <button className="archive-link" onClick={onLibrary}>典藏卡册 <b>{characters.length}</b></button>
    </header>

    {interrupted && <button className="interrupted-run-card" onClick={() => onResume(interrupted.session_id)}>
      <span>待返回会话</span>
      <strong>{interrupted.title}</strong>
      <small>上次回答在此处中断，已生成内容仍然保留</small>
      <b>继续查看 →</b>
    </button>}

    <section className="mode-card-grid">
      <button className="mode-card draw-card" onClick={onDraw}>
        <div className="mode-card-ornament" aria-hidden="true">✦</div>
        <span>QUICK START · 灵感构筑</span>
        <h2>灵感抽卡</h2>
        <p>只需给出名字、性格、缺陷和关系，让 AI 帮你补全一张可编辑的人物卡。</p>
        <footer>
          <small>{drawCount ? `已有 ${drawCount} 张抽卡角色` : "约 3 分钟完成首张卡"}</small>
          <b>{drawCount ? "继续抽卡" : "开始构筑"} →</b>
        </footer>
      </button>
      <button className="mode-card custom-card" onClick={onCustom}>
        <div className="mode-card-ornament" aria-hidden="true">◇</div>
        <span>FULL CONTROL · 完整配置</span>
        <h2>自定义模式</h2>
        <p>进入现有完整工作台，继续使用档案、知识库、Prompt Inspector 与语音能力。</p>
        <footer>
          <small>适合已经有角色卡的用户</small>
          <b>选择角色进入 →</b>
        </footer>
      </button>
    </section>
    <div className="mode-lobby-footnote">
      <span>本地优先</span><span>多角色隔离</span><span>可导入导出</span><span>可随时返回大厅</span>
    </div>
  </main>;
}

export function CharacterPicker({
  open,
  characters,
  title = "选择本次对话的角色",
  onClose,
  onChoose,
  onDraw,
}: {
  open: boolean;
  characters: CharacterSummary[];
  title?: string;
  onClose: () => void;
  onChoose: (character: CharacterSummary) => void;
  onDraw: () => void;
}) {
  if (!open) return null;
  return <div className="modal-backdrop character-picker-backdrop">
    <section className="character-picker" role="dialog" aria-modal="true" aria-label={title}>
      <header>
        <div><span className="eyebrow">CHARACTER BINDING</span><h2>{title}</h2><p>会话一旦产生消息，就不能静默换绑角色。</p></div>
        <button onClick={onClose} aria-label="关闭">×</button>
      </header>
      <div className="character-picker-grid">
        {characters.map((character) => <button key={character.character_id} onClick={() => onChoose(character)}>
          <img src={characterAvatar(character)} alt="" />
          <span><strong>{character.display_name}</strong><small>{character.relationship_label || "未定义关系"} · {character.source === "draw" ? "灵感抽卡" : "自定义"}</small></span>
          <b>选择</b>
        </button>)}
        <button className="character-picker-new" onClick={onDraw}>
          <i>＋</i><span><strong>创建新角色</strong><small>前往灵感抽卡</small></span>
        </button>
      </div>
    </section>
  </div>;
}

export function DrawWorkshop({
  defaultUserName,
  onBack,
  onCommitted,
}: {
  defaultUserName: string;
  onBack: () => void;
  onCommitted: (character: CharacterRecord) => void;
}) {
  const [step, setStep] = useState(1);
  const [options, setOptions] = useState<CharacterOptions | null>(null);
  const [input, setInput] = useState<CharacterInput>(() => ({
    ...DEFAULT_INPUT,
    user_name: defaultUserName || "用户",
  }));
  const [draft, setDraft] = useState<CharacterDraft | null>(null);
  const [profileText, setProfileText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [customTrait, setCustomTrait] = useState("");
  const [traitNotice, setTraitNotice] = useState("");
  const [pendingAvatar, setPendingAvatar] = useState<File | null>(null);
  const [pendingAvatarUrl, setPendingAvatarUrl] = useState("");
  const [selectedAvatar, setSelectedAvatar] = useState("/assets/characters/placeholder-1.webp");
  const fileRef = useRef<HTMLInputElement | null>(null);
  const placeholderAvatar = selectedAvatar;

  useEffect(() => {
    request<CharacterOptions>("/api/v1/characters/options")
      .then(setOptions)
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const optionMap = useMemo(() => {
    const result = new Map<string, CharacterOption>();
    for (const item of [...(options?.core_traits || []), ...(options?.flaws || [])]) {
      result.set(item.label, item);
    }
    return result;
  }, [options]);

  const selectedIds = useMemo(() => new Set(
    [...input.core_traits, input.flaw]
      .map((label) => optionMap.get(label)?.id)
      .filter(Boolean),
  ), [input, optionMap]);

  const disabledReason = (option: CharacterOption) => {
    const selected = [...input.core_traits, input.flaw];
    const conflicts = selected.filter((label) => {
      const current = optionMap.get(label);
      return option.conflicts?.includes(current?.id || "")
        || current?.conflicts?.includes(option.id);
    });
    return conflicts.length ? `与“${conflicts[0]}”冲突` : "";
  };

  const toggleTrait = (label: string) => {
    setError("");
    setTraitNotice("");
    if (input.core_traits.includes(label)) {
      setInput((current) => ({
        ...current,
        core_traits: current.core_traits.filter((item) => item !== label),
      }));
      return true;
    }
    if (input.core_traits.length >= 2) {
      setTraitNotice("已经选满 2 项，请先取消一项，再加入新的核心性格。");
      return false;
    }
    setInput((current) => ({
      ...current,
      core_traits: [...current.core_traits, label],
    }));
    return true;
  };

  const addCustomTrait = () => {
    const label = customTrait.trim();
    if (!label) return;
    if (input.core_traits.includes(label)) {
      setTraitNotice(`“${label}”已经在当前选择中。`);
      return;
    }
    if (toggleTrait(label)) setCustomTrait("");
  };

  const randomize = () => {
    if (!options) return;
    const shuffled = [...options.core_traits].sort(() => Math.random() - 0.5);
    let pair: CharacterOption[] = [];
    for (const first of shuffled) {
      const second = shuffled.find((candidate) =>
        candidate.id !== first.id
        && !first.conflicts?.includes(candidate.id)
        && !candidate.conflicts?.includes(first.id));
      if (second) {
        pair = [first, second];
        break;
      }
    }
    const flaws = options.flaws.filter((item) =>
      !pair.some((trait) => item.conflicts?.includes(trait.id) || trait.conflicts?.includes(item.id)));
    const flaw = flaws[Math.floor(Math.random() * Math.max(1, flaws.length))];
    setInput((current) => ({
      ...current,
      core_traits: pair.map((item) => item.label),
      flaw: flaw?.label || "",
    }));
  };

  const validateStep = () => {
    if (step === 1 && !input.ai_name.trim()) return "请先填写 AI 名称";
    if (step === 2 && (input.core_traits.length !== 2 || !input.flaw)) return "请选择两个核心性格和一个人格缺陷";
    if (step === 3 && (!input.relationship.trim() || !input.user_name.trim())) return "请补全关系和用户名称";
    return "";
  };

  const next = () => {
    const reason = validateStep();
    if (reason) {
      setError(reason);
      return;
    }
    setError("");
    setStep((current) => Math.min(4, current + 1));
  };

  const generate = async () => {
    setBusy(true);
    setError("");
    try {
      let current = draft;
      if (!current) {
        current = await request<CharacterDraft>("/api/v1/character-drafts", {
          method: "POST",
          body: JSON.stringify(input),
        });
      } else {
        current = await request<CharacterDraft>(`/api/v1/character-drafts/${encodeURIComponent(current.draft_id)}`, {
          method: "PUT",
          body: JSON.stringify({ input }),
        });
      }
      current = await request<CharacterDraft>(`/api/v1/character-drafts/${encodeURIComponent(current.draft_id)}/generate`, {
        method: "POST",
      });
      if (pendingAvatar) {
        const body = new FormData();
        body.append("file", pendingAvatar);
        const uploaded = await request<{ draft: CharacterDraft }>(
          `/api/v1/character-drafts/${encodeURIComponent(current.draft_id)}/avatar`,
          { method: "POST", body },
        );
        current = uploaded.draft;
        setPendingAvatar(null);
      } else {
        current = await request<CharacterDraft>(
          `/api/v1/character-drafts/${encodeURIComponent(current.draft_id)}`,
          {
            method: "PUT",
            body: JSON.stringify({
              avatar: { src: selectedAvatar, aspect: "2 / 3", scale: 1, x: 0, y: 0 },
            }),
          },
        );
      }
      setDraft(current);
      setProfileText(JSON.stringify(current.profile, null, 2));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (step === 4 && !draft && !busy) void generate();
  // generate intentionally depends on the confirmed step transition, not each input keystroke.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  const uploadAvatar = async (file?: File) => {
    if (!file) return;
    if (!draft) {
      setPendingAvatar(file);
      setPendingAvatarUrl(URL.createObjectURL(file));
      return;
    }
    const body = new FormData();
    body.append("file", file);
    setBusy(true);
    setError("");
    try {
      const result = await request<{ draft: CharacterDraft }>(
        `/api/v1/character-drafts/${encodeURIComponent(draft.draft_id)}/avatar`,
        { method: "POST", body },
      );
      setDraft(result.draft);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      const profile = JSON.parse(profileText) as Record<string, unknown>;
      const result = await request<{ character: CharacterRecord }>(
        `/api/v1/character-drafts/${encodeURIComponent(draft.draft_id)}/commit`,
        { method: "POST", body: JSON.stringify({ profile }) },
      );
      onCommitted(result.character);
    } catch (reason) {
      setError(reason instanceof SyntaxError ? "完整 JSON 格式不正确，现有草稿没有被覆盖" : (reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const identity = asRecord(draft?.profile.identity);
  const personality = asRecord(draft?.profile.personality);
  const relationship = asRecord(draft?.profile.relationship_rules);

  return <main className="draw-workshop">
    <header className="workshop-topbar">
      <button onClick={onBack}>← 返回模式大厅</button>
      <div><span className="eyebrow">INSPIRATION FORGE</span><strong>灵感抽卡工坊</strong></div>
      <small>草稿只在确认收藏后写入角色库</small>
    </header>
    <div className="workshop-layout">
      <aside className="workshop-steps">
        <span>创建进度</span>
        {[
          ["01", "形象与名字"],
          ["02", "性格与缺陷"],
          ["03", "关系与称呼"],
          ["04", "生成与收藏"],
        ].map(([number, label], index) => <button
          key={number}
          className={`${step === index + 1 ? "active" : ""}${step > index + 1 ? " complete" : ""}`}
          onClick={() => index + 1 < step && setStep(index + 1)}
        ><b>{step > index + 1 ? "✓" : number}</b><span>{label}</span></button>)}
        <div className="workshop-tip"><b>一次生成</b><p>每次抽卡最多调用一次 LLM。失败时会使用本地合法模板，不会反复重试。</p></div>
      </aside>
      <section className="workshop-panel">
        {step === 1 && <>
          <span className="eyebrow">STEP 01</span><h1>先让角色有一个名字</h1><p>头像是可选项，生成草稿后仍可上传并调整。</p>
          <div className="identity-editor">
            <button className="avatar-drop" onClick={() => fileRef.current?.click()}>
              <img src={pendingAvatarUrl || (draft?.avatar && "src" in draft.avatar ? text(draft.avatar.src) : placeholderAvatar)} alt="" />
              <span>{pendingAvatarUrl || draft ? "更换头像" : "上传头像（可选）"}</span>
            </button>
            <input ref={fileRef} hidden type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={(event) => void uploadAvatar(event.target.files?.[0])} />
            <div>
              <label>AI 名称<input value={input.ai_name} maxLength={80} onChange={(event) => setInput({ ...input, ai_name: event.target.value })} placeholder="例如：林见月" /></label>
              <fieldset><legend>AI 性别</legend>{(options?.gender || ["女", "男"]).map((item) => <button key={item} className={input.ai_gender === item ? "selected" : ""} onClick={() => { setInput({ ...input, ai_gender: item }); setSelectedAvatar(item === "女" ? "/assets/characters/placeholder-1.webp" : "/assets/characters/placeholder-2.webp"); setPendingAvatarUrl(""); setPendingAvatar(null); }}>{item}</button>)}</fieldset>
              <div className="placeholder-avatar-picker">
                <span>原创占位头像</span>
                {(input.ai_gender === "女" ? [1, 3] : [2, 4]).map((index) => {
                  const src = `/assets/characters/placeholder-${index}.webp`;
                  return <button key={src} className={selectedAvatar === src && !pendingAvatarUrl ? "selected" : ""} onClick={() => { setSelectedAvatar(src); setPendingAvatar(null); setPendingAvatarUrl(""); }}><img src={src} alt={`占位头像 ${index}`} /></button>;
                })}
              </div>
            </div>
          </div>
        </>}
        {step === 2 && <>
          <span className="eyebrow">STEP 02</span><h1>人格要有棱角，也要有缺口</h1><p>选择两个核心性格和一个真实缺陷。明显冲突的组合会被禁用并说明原因。</p>
          <div className="selection-heading"><strong>核心性格 · 选 2 项</strong><button onClick={randomize}>↻ 刷新合法组合</button></div>
          <div className="trait-grid">{options?.core_traits.map((item) => {
            const reason = disabledReason(item);
            const selected = input.core_traits.includes(item.label);
            return <button key={item.id} className={selected ? "selected" : ""} disabled={!selected && Boolean(reason)} title={reason} onClick={() => toggleTrait(item.label)}><i>{selected ? "✓" : "◇"}</i><span>{item.label}<small>{reason || "可与其他性格组合"}</small></span></button>;
          })}</div>
          <div className="custom-trait-entry"><input value={customTrait} onChange={(event) => { setCustomTrait(event.target.value); setTraitNotice(""); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); addCustomTrait(); } }} placeholder="自定义核心性格" /><button disabled={!customTrait.trim()} onClick={addCustomTrait}>加入选择</button></div>
          {input.core_traits.filter((label) => !optionMap.has(label)).length > 0 && <div className="custom-trait-selections" aria-label="已选择的自定义核心性格">
            {input.core_traits.filter((label) => !optionMap.has(label)).map((label) => <button key={label} onClick={() => toggleTrait(label)} title="点击取消选择"><span>自定义</span>{label}<b>×</b></button>)}
          </div>}
          {traitNotice && <div className="trait-selection-notice" role="status">{traitNotice}</div>}
          <div className="selection-heading"><strong>人格缺陷 · 选 1 项</strong></div>
          <div className="flaw-grid">{options?.flaws.map((item) => {
            const reason = disabledReason(item);
            return <button key={item.id} className={input.flaw === item.label ? "selected" : ""} disabled={Boolean(reason) && input.flaw !== item.label} title={reason} onClick={() => setInput({ ...input, flaw: item.label })}>{item.label}</button>;
          })}</div>
          <label className="custom-option">也可以自己写缺陷<input value={input.flaw} onChange={(event) => setInput({ ...input, flaw: event.target.value })} placeholder="例如：在亲密关系中容易没有安全感" /></label>
        </>}
        {step === 3 && <>
          <span className="eyebrow">STEP 03</span><h1>定义你们如何认识彼此</h1><p>用户名称全局共享；角色专属称呼只属于这张角色卡。</p>
          <label>你们的关系<select value={(options?.relationships || []).includes(input.relationship) ? input.relationship : "__custom__"} onChange={(event) => setInput({ ...input, relationship: event.target.value === "__custom__" ? "" : event.target.value })}>{options?.relationships.map((item) => <option key={item}>{item}</option>)}<option value="__custom__">自定义关系</option></select></label>
          {!(options?.relationships || []).includes(input.relationship) && <label>自定义关系<input value={input.relationship} onChange={(event) => setInput({ ...input, relationship: event.target.value })} placeholder="例如：在同一座城市生活的伴侣" /></label>}
          <div className="two-column-fields">
            <label>用户名称<input value={input.user_name} onChange={(event) => setInput({ ...input, user_name: event.target.value })} /></label>
            <label>角色如何称呼你<input value={input.user_alias} onChange={(event) => setInput({ ...input, user_alias: event.target.value })} placeholder="可留空" /></label>
          </div>
          <div className="relationship-seal-preview"><span>关系印章</span><strong>{input.relationship || "未定义"}</strong><small>{input.ai_name || "AI"} × {input.user_alias || input.user_name || "用户"}</small></div>
        </>}
        {step === 4 && <>
          <span className="eyebrow">STEP 04</span><h1>预览这张人物卡</h1>
          {busy && <div className="card-generating"><i /><strong>正在构筑角色</strong><small>API 不可用时会自动采用本地模板</small></div>}
          {!busy && draft && <div className="character-preview-card">
            <div className="preview-portrait"><img src={draft.avatar && "src" in draft.avatar ? text(draft.avatar.src) : placeholderAvatar} alt="" /><span>{draft.generation_mode === "llm" ? "AI 辅助生成" : "本地模板生成"}</span></div>
            <div><span className="eyebrow">{input.ai_gender} · {input.relationship}</span><h2>{text(identity.name) || input.ai_name}</h2><p>{text(identity.self_description)}</p><dl><div><dt>性格</dt><dd>{Array.isArray(personality.core_traits) ? personality.core_traits.join(" · ") : input.core_traits.join(" · ")}</dd></div><div><dt>缺陷</dt><dd>{input.flaw}</dd></div><div><dt>关系</dt><dd>{text(relationship.relationship_definition) || input.relationship}</dd></div></dl></div>
          </div>}
          {draft?.warnings?.length ? <div className="draft-warning">{draft.warnings.join("；")}</div> : null}
          <details className="advanced-json" open={advanced} onToggle={(event) => setAdvanced(event.currentTarget.open)}>
            <summary>高级编辑 · 完整 JSON</summary>
            <textarea value={profileText} onChange={(event) => setProfileText(event.target.value)} spellCheck={false} />
            <small>格式错误不会覆盖当前草稿，也不会破坏已有角色。</small>
          </details>
        </>}
        {error && <div className="workshop-error">{error}</div>}
        <footer className="workshop-actions">
          <button className="secondary" onClick={() => step === 1 ? onBack() : setStep(step - 1)}>{step === 1 ? "返回大厅" : "返回上一步"}</button>
          {step < 4 ? <button className="primary" onClick={next}>继续</button> : <>
            <button className="secondary" onClick={() => void generate()} disabled={busy}>重新生成草稿</button>
            <button className="primary" onClick={() => void commit()} disabled={busy || !draft}>确认收藏并开始对话</button>
          </>}
        </footer>
      </section>
    </div>
  </main>;
}

export function CharacterLibrary({
  characters,
  onBack,
  onRefresh,
  onChat,
  onDraw,
}: {
  characters: CharacterSummary[];
  onBack: () => void;
  onRefresh: () => Promise<void>;
  onChat: (character: CharacterSummary) => void;
  onDraw: () => void;
}) {
  const [selectedId, setSelectedId] = useState(characters[0]?.character_id || "");
  const [record, setRecord] = useState<CharacterRecord | null>(null);
  const [editText, setEditText] = useState("");
  const [history, setHistory] = useState<Array<{ version_id: string; revision: number; updated_at: string }>>([]);
  const [error, setError] = useState("");
  const importRef = useRef<HTMLInputElement | null>(null);
  const selectedSummary = characters.find((item) => item.character_id === selectedId);

  useEffect(() => {
    if (!selectedId) {
      setRecord(null);
      return;
    }
    Promise.all([
      request<CharacterRecord>(`/api/v1/characters/${encodeURIComponent(selectedId)}`),
      request<{ items: Array<{ version_id: string; revision: number; updated_at: string }> }>(`/api/v1/characters/${encodeURIComponent(selectedId)}/history`),
    ]).then(([value, versions]) => {
      setRecord(value);
      setEditText(JSON.stringify(value.ai_profile, null, 2));
      setHistory(versions.items);
      setError("");
    }).catch((reason: Error) => setError(reason.message));
  }, [selectedId]);

  useEffect(() => {
    if (!characters.some((item) => item.character_id === selectedId)) {
      setSelectedId(characters[0]?.character_id || "");
    }
  }, [characters, selectedId]);

  const mutate = async (path: string, body?: unknown) => {
    setError("");
    try {
      await request(path, { method: "POST", body: body == null ? undefined : JSON.stringify(body) });
      await onRefresh();
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  const save = async () => {
    if (!record) return;
    try {
      const profile = JSON.parse(editText);
      const result = await request<{ character: CharacterRecord }>(`/api/v1/characters/${encodeURIComponent(record.character_id)}`, {
        method: "PUT",
        body: JSON.stringify({ revision: record.revision, ai_profile: profile }),
      });
      setRecord(result.character);
      setEditText(JSON.stringify(result.character.ai_profile, null, 2));
      await onRefresh();
    } catch (reason) {
      setError(reason instanceof SyntaxError ? "JSON 格式不正确，未保存" : (reason as Error).message);
    }
  };

  const importCard = async (file?: File) => {
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await request<{ character: CharacterRecord }>("/api/v1/characters/import", { method: "POST", body });
      await onRefresh();
      setSelectedId(result.character.character_id);
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  return <main className="character-library">
    <header>
      <button onClick={onBack}>← 返回模式大厅</button>
      <div><span className="eyebrow">COLLECTOR'S ARCHIVE</span><h1>典藏卡册</h1><p>每张卡拥有独立档案、运行状态、共同经历和对话记忆。</p></div>
      <div><button onClick={() => importRef.current?.click()}>导入卡包</button><button className="primary" onClick={onDraw}>＋ 灵感抽卡</button><input ref={importRef} hidden type="file" accept=".mindspace-card" onChange={(event) => void importCard(event.target.files?.[0])} /></div>
    </header>
    <div className="library-layout">
      <nav className="card-shelf">
        {characters.map((character) => <button key={character.character_id} className={selectedId === character.character_id ? "active" : ""} onClick={() => setSelectedId(character.character_id)}>
          <img src={characterAvatar(character)} alt="" />
          <span><strong>{character.display_name}</strong><small>{character.relationship_label || "未定义关系"} · {character.session_count || 0} 个会话</small><small>日记 {character.chapters?.journal_count || 0} · 片段 {character.chapters?.moment_count || 0} · 活动 {character.chapters?.activity_count || 0}</small></span>
          <i>{character.status === "archived" ? "已归档" : character.source === "draw" ? "灵感" : "自定义"}</i>
        </button>)}
        {!characters.length && <div className="library-empty"><b>还没有收藏角色</b><p>先抽取第一张卡，或导入 `.mindspace-card`。</p><button onClick={onDraw}>开始抽卡</button></div>}
      </nav>
      <section className="library-detail">
        {record ? <>
          <div className="library-hero">
            <img src={characterAvatar(record)} alt="" />
            <div><span>{record.gender} · {record.relationship_label}</span><h2>{record.display_name}</h2><p>{text(asRecord(record.ai_profile.identity).self_description)}</p><small>修订 {record.revision} · 更新于 {new Date(record.updated_at).toLocaleString()}</small><small className="chapter-counts">日记 {selectedSummary?.chapters?.journal_count || 0} · 共同片段 {selectedSummary?.chapters?.moment_count || 0} · 已完成活动 {selectedSummary?.chapters?.activity_count || 0}</small></div>
          </div>
          <div className="library-actions">
            <button className="primary" onClick={() => onChat(record)}>开始新对话</button>
            <a href={`/api/v1/characters/${encodeURIComponent(record.character_id)}/export`} download>导出卡包</a>
            <button onClick={() => void mutate(`/api/v1/characters/${encodeURIComponent(record.character_id)}/clone`)}>复制角色</button>
            <button onClick={() => void mutate(`/api/v1/characters/${encodeURIComponent(record.character_id)}/archive`)}>{record.status === "archived" ? "取消归档" : "归档"}</button>
          </div>
          <details className="library-json"><summary>编辑完整人物卡</summary><textarea value={editText} onChange={(event) => setEditText(event.target.value)} spellCheck={false} /><button onClick={() => void save()}>校验并保存新版本</button></details>
          <details className="library-history"><summary>版本历史 · {history.length}</summary>{history.map((item) => <div key={item.version_id}><span>修订 {item.revision}<small>{new Date(item.updated_at).toLocaleString()}</small></span><button onClick={() => void mutate(`/api/v1/characters/${encodeURIComponent(record.character_id)}/restore`, { version_id: item.version_id, expected_revision: record.revision })}>恢复此版本</button></div>)}</details>
        </> : <div className="library-empty"><b>选择一张角色卡</b><p>这里会显示人物设定、版本与卡包操作。</p></div>}
        {error && <div className="workshop-error">{error}</div>}
      </section>
    </div>
  </main>;
}

export type { AppView };
