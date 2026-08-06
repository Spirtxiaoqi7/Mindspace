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
  | "scenes";

interface CharacterOption {
  id: string;
  label: string;
  conflicts?: string[];
}

interface CharacterOptions {
  core_traits: CharacterOption[];
  flaws: CharacterOption[];
  relationships: string[];
  gender: Array<"男" | "女" | "不指定">;
  fate_system: FateCatalog;
}

type FateRarity = "red" | "blue" | "gold";
type FateAnswer = "yes" | "no" | "custom";

interface FateCandidate {
  id: string;
  rarity: FateRarity;
  title: string;
  summary: string;
  question?: string;
  yes_direction?: string;
  no_direction?: string;
}

interface FateSlot {
  id: string;
  index: number;
  title: string;
  short_title: string;
  icon: string;
  description: string;
}

interface FateCatalog {
  schema_version: string;
  rarities: Record<FateRarity, { label: string; meaning: string }>;
  slots: FateSlot[];
}

interface FateAnswerValue { answer: FateAnswer; custom: string }

interface GeneratedFateOptions {
  schema_version: string;
  options: Record<string, FateCandidate[]>;
}

interface CharacterInput {
  ai_name: string;
  ai_gender: "男" | "女" | "不指定";
  core_traits: string[];
  flaw: string;
  relationship: string;
  relationship_context: string;
  user_name: string;
  user_alias: string;
  fate_forge?: Record<string, unknown>;
}

const DEFAULT_INPUT: CharacterInput = {
  ai_name: "",
  ai_gender: "女",
  core_traits: [],
  flaw: "",
  relationship: "朋友",
  relationship_context: "",
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
  userName,
  interrupted,
  onDraw,
  onCustom,
  onLibrary,
  onResume,
}: {
  characters: CharacterSummary[];
  userName: string;
  interrupted?: { session_id: string; title: string };
  onDraw: () => void;
  onCustom: () => void;
  onLibrary: () => void;
  onResume: (sessionId: string) => void;
}) {
  const drawCount = characters.filter((item) => item.source === "draw").length;
  const primary = [...characters].sort((a, b) => b.last_used_at.localeCompare(a.last_used_at))[0];
  const pairLine = primary
    ? `${primary.user_alias || userName || "你"}，我在这里。今天想从哪里继续？`
    : "先构筑一位角色，让下一次打开不再从空白开始。";
  return <main className="mode-lobby">
    <header className="mode-lobby-header">
      <div className="brand-mark"><img src="/assets/mindspace-brand-icon.png" alt="" /></div>
      <div>
        <span className="eyebrow">MINDSPACE · COMPANION HOME</span>
        <h1>{primary ? `${userName || "你"} × ${primary.display_name}` : "建立你们的第一段关系"}</h1>
        <p>{pairLine}</p>
      </div>
      <button className="archive-link" onClick={onLibrary}>人设 <b>{characters.length}</b></button>
    </header>

    {interrupted && <button className="interrupted-run-card" onClick={() => onResume(interrupted.session_id)}>
      <span>待返回会话</span>
      <strong>{interrupted.title}</strong>
      <small>上次回答在此处中断，已生成内容仍然保留</small>
      <b>继续查看 →</b>
    </button>}

    {primary && <section className="pair-home-stage">
      <div className="pair-home-copy">
        <span>{primary.relationship_label || "陪伴关系"}</span>
        <blockquote>“{pairLine}”</blockquote>
        <div className="pair-home-actions">
          <button onClick={() => primary.latest_session_id && onResume(primary.latest_session_id)} disabled={!primary.latest_session_id}>继续最近对话 <b>↗</b></button>
          <button onClick={onCustom}>切换人物</button>
        </div>
      </div>
      <img src={characterAvatar(primary)} alt={primary.display_name} />
    </section>}

    <section className="mode-card-grid">
      <button className="mode-card draw-card" onClick={onDraw}>
        <div className="mode-card-ornament" aria-hidden="true">✦</div>
        <span>FATE SYSTEM · 十二命格</span>
        <h2>命定系统</h2>
        <p>铸造十二条命格，再由 AI 合成为拥有欲望、关系、生活与变化轨迹的角色。</p>
        <footer>
          <small>{drawCount ? `已有 ${drawCount} 位命定角色` : "约 3 分钟完成首次命定"}</small>
          <b>{drawCount ? "再次铸命" : "开始铸命"} →</b>
        </footer>
      </button>
      <button className="mode-card custom-card" onClick={onCustom}>
        <div className="mode-card-ornament" aria-hidden="true">◇</div>
        <span>CONTINUE · 选择已有角色</span>
        <h2>人物与关系</h2>
        <p>选择一位已有角色，恢复TA最近的会话；人物档案与 API 设置始终在同一处。</p>
        <footer>
          <small>不会因为点击再次创建聊天窗口</small>
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
        <div><span className="eyebrow">CHARACTER</span><h2>{title}</h2><p>已有对话会恢复到最近位置；只有首次进入才创建会话。</p></div>
        <button onClick={onClose} aria-label="关闭">×</button>
      </header>
      <div className="character-picker-grid">
        {characters.map((character) => <button key={character.character_id} onClick={() => onChoose(character)}>
          <img src={characterAvatar(character)} alt="" />
          <span><strong>{character.display_name}</strong><small>{character.relationship_label || "未定义关系"} · {character.source === "draw" ? "命定系统" : "自定义"}</small></span>
          <b>{character.latest_session_id ? "继续" : "开始"}</b>
        </button>)}
        <button className="character-picker-new" onClick={onDraw}>
          <i>＋</i><span><strong>创建新角色</strong><small>前往命定系统</small></span>
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
  const [pendingAvatar, setPendingAvatar] = useState<File | null>(null);
  const [pendingAvatarUrl, setPendingAvatarUrl] = useState("");
  const [selectedAvatar, setSelectedAvatar] = useState("/assets/characters/placeholder-1.webp");
  const [selectedBlockIds, setSelectedBlockIds] = useState<string[]>([]);
  const [rewriteInstruction, setRewriteInstruction] = useState("");
  const [fateCandidates, setFateCandidates] = useState<Record<string, FateCandidate[]>>({});
  const [fateSelections, setFateSelections] = useState<Record<string, FateCandidate>>({});
  const [fateAnswers, setFateAnswers] = useState<Record<string, FateAnswerValue>>({});
  const [fateRolls, setFateRolls] = useState(0);
  const [fateModification, setFateModification] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);
  const placeholderAvatar = selectedAvatar;

  useEffect(() => {
    request<CharacterOptions>("/api/v1/characters/options")
      .then((value) => {
        setOptions(value);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const generateFateOptions = async (slotIds: string[] = []) => {
    setBusy(true);
    setError("");
    try {
      const result = await request<GeneratedFateOptions>("/api/v1/characters/fate-options", {
        method: "POST",
        body: JSON.stringify({
          relationship: input.relationship.trim(),
          user_content: input.relationship_context.trim(),
          modification: fateModification.trim(),
          slot_ids: slotIds,
        }),
      });
      setFateCandidates((current) => slotIds.length ? { ...current, ...result.options } : result.options);
      setFateSelections((current) => {
        if (!slotIds.length) return {};
        const next = { ...current };
        for (const slotId of slotIds) delete next[slotId];
        return next;
      });
      setFateAnswers((current) => {
        const retainedIds = Object.values(fateSelections)
          .filter((item) => !slotIds.length || !slotIds.some((slotId) => fateSelections[slotId]?.id === item.id))
          .map((item) => item.id);
        return Object.fromEntries(Object.entries(current).filter(([id]) => retainedIds.includes(id)));
      });
      setFateRolls((current) => current + 1);
      setFateModification("");
      return true;
    } catch (reason) {
      setError((reason as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const rerollFate = (slot: FateSlot) => void generateFateOptions([slot.id]);

  const chooseFate = (slot: FateSlot, fate: FateCandidate) => {
    setFateSelections((current) => ({ ...current, [slot.id]: fate }));
  };

  const updateFateAnswer = (fateId: string, answer: FateAnswer, custom?: string) => {
    setFateAnswers((current) => ({
      ...current,
      [fateId]: { answer, custom: custom ?? current[fateId]?.custom ?? "" },
    }));
  };

  const completedFates = Object.keys(fateSelections).length;
  const selectedGold = Object.values(fateSelections).filter((item) => item.rarity === "gold");
  const selectedRed = Object.values(fateSelections).filter((item) => item.rarity === "red");

  const compiledInput = () => {
    const slots = options?.fate_system.slots || [];
    const ordered = slots.map((slot) => ({ slot, fate: fateSelections[slot.id] }));
    const nonRed = ordered.filter((item) => item.fate?.rarity !== "red").map((item) => item.fate?.title).filter(Boolean) as string[];
    const red = ordered.filter((item) => item.fate?.rarity === "red").map((item) => item.fate?.title).filter(Boolean) as string[];
    return {
      ...input,
      core_traits: [...nonRed, "关系取向明确", "表达具体"].slice(0, 2),
      flaw: red[0] || fateSelections.paradox?.title || "有真实矛盾",
      fate_forge: {
        schema_version: options?.fate_system.schema_version || "2.0.0",
        seed: `fate-${Date.now()}-${fateRolls}`,
        relationship: input.relationship,
        user_content: input.relationship_context,
        selections: ordered.map(({ slot, fate }) => ({
          slot_id: slot.id,
          fate_id: fate?.id,
          rarity: fate?.rarity,
          title: fate?.title,
          summary: fate?.summary,
          question: fate?.question,
          yes_direction: fate?.yes_direction,
          no_direction: fate?.no_direction,
          answer: fate?.rarity === "gold" ? fateAnswers[fate.id]?.answer : "",
          custom: fate?.rarity === "gold" ? fateAnswers[fate.id]?.custom : "",
        })),
      },
    };
  };

  const validateStep = () => {
    if (step === 1 && !input.ai_name.trim()) return "请先填写 AI 名称";
    if (step === 1 && !options) return "命格目录正在载入，请稍候";
    if (step === 2 && (!input.relationship.trim() || !input.user_name.trim() || !input.relationship_context.trim())) return "请补全关系、用户名称和你对角色的描述";
    if (step === 3 && completedFates !== 12) return `还需锁定 ${12 - completedFates} 个命格槽位`;
    if (step === 3 && selectedGold.some((fate) => {
      const answer = fateAnswers[fate.id];
      return !answer || (answer.answer === "custom" && !answer.custom.trim());
    })) return "请完成全部金色命格的是、否或自定义问命";
    return "";
  };

  const next = async () => {
    const reason = validateStep();
    if (reason) {
      setError(reason);
      return;
    }
    setError("");
    if (step === 2) {
      const generated = await generateFateOptions();
      if (!generated) return;
    }
    setStep((current) => Math.min(5, current + 1));
  };

  const generate = async () => {
    setBusy(true);
    setError("");
    try {
      const payload = compiledInput();
      let current = draft;
      if (!current) {
        current = await request<CharacterDraft>("/api/v1/character-drafts", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      } else {
        current = await request<CharacterDraft>(`/api/v1/character-drafts/${encodeURIComponent(current.draft_id)}`, {
          method: "PUT",
          body: JSON.stringify({ input: payload }),
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
    if (step === 5 && !draft && !busy) void generate();
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

  const toggleBlueprintBlock = (blockId: string) => {
    setSelectedBlockIds((current) => current.includes(blockId)
      ? current.filter((item) => item !== blockId)
      : [...current, blockId]);
  };

  const rewriteSelectedBlocks = async () => {
    if (!draft || !selectedBlockIds.length) {
      setError("请至少选择一个需要重写的板块");
      return;
    }
    if (!rewriteInstruction.trim()) {
      setError("请告诉 AI 你希望怎样调整所选板块");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const current = await request<CharacterDraft>(
        `/api/v1/character-drafts/${encodeURIComponent(draft.draft_id)}/rewrite`,
        {
          method: "POST",
          body: JSON.stringify({
            block_ids: selectedBlockIds,
            instruction: rewriteInstruction.trim(),
          }),
        },
      );
      setDraft(current);
      setProfileText(JSON.stringify(current.profile, null, 2));
      if (!current.warnings?.length) {
        setSelectedBlockIds([]);
        setRewriteInstruction("");
      }
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const identity = asRecord(draft?.profile.identity);
  const personality = asRecord(draft?.profile.personality);
  const relationship = asRecord(draft?.profile.relationship_rules);

  return <main className="draw-workshop">
    <header className="workshop-topbar">
      <button onClick={onBack}>← 返回主页</button>
      <div><span className="eyebrow">FATE SYSTEM · DESTINY FORGE</span><strong>命定系统</strong></div>
      <small>草稿只在确认收藏后写入角色库</small>
    </header>
    <div className="workshop-layout">
      <aside className="workshop-steps">
        <span>创建进度</span>
        {[
          ["01", "缔结身份"],
          ["02", "关系契约"],
          ["03", "铸造命盘"],
          ["04", "合相定格"],
          ["05", "人设成型"],
        ].map(([number, label], index) => <button
          key={number}
          className={`${step === index + 1 ? "active" : ""}${step > index + 1 ? " complete" : ""}`}
          onClick={() => index + 1 < step && setStep(index + 1)}
        ><b>{step > index + 1 ? "✓" : number}</b><span>{label}</span></button>)}
        <div className="workshop-tip"><b>十二命格</b><p>红色写入代价，蓝色稳定日常，金色通过是、否、自定义确定唯一方向。</p></div>
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
              <fieldset><legend>AI 性别</legend>{(options?.gender || ["女", "男", "不指定"]).map((item) => <button key={item} className={input.ai_gender === item ? "selected" : ""} onClick={() => { setInput({ ...input, ai_gender: item }); setSelectedAvatar(item === "男" ? "/assets/characters/placeholder-2.webp" : "/assets/characters/placeholder-1.webp"); setPendingAvatarUrl(""); setPendingAvatar(null); }}>{item}</button>)}</fieldset>
              <div className="placeholder-avatar-picker">
                <span>原创占位头像</span>
                {(input.ai_gender === "女" ? [1, 3] : input.ai_gender === "男" ? [2, 4] : [1, 2, 3, 4]).map((index) => {
                  const src = `/assets/characters/placeholder-${index}.webp`;
                  return <button key={src} className={selectedAvatar === src && !pendingAvatarUrl ? "selected" : ""} onClick={() => { setSelectedAvatar(src); setPendingAvatar(null); setPendingAvatarUrl(""); }}><img src={src} alt={`占位头像 ${index}`} /></button>;
                })}
              </div>
            </div>
          </div>
        </>}
        {step === 3 && <>
          <span className="eyebrow">STEP 03 · DESTINY MATRIX</span><h1>选择这次实时生成的命格</h1>
          <p>这些候选只来自你刚才写下的关系和角色描述。修改想法后可以重铸全部或单独重铸一槽。</p>
          <div className="fate-live-revision">
            <textarea value={fateModification} maxLength={1200} onChange={(event) => setFateModification(event.target.value)} placeholder="可选：告诉 AI 这次想怎样调整候选……" />
            <button disabled={busy} onClick={() => void generateFateOptions()}>{busy ? "生成中…" : "按新想法重铸全部"}</button>
          </div>
          <div className="fate-forge-summary">
            <span><b>{completedFates}</b>/12 已定</span><span className="red"><b>{selectedRed.length}</b> 红命</span><span className="gold"><b>{selectedGold.length}</b> 金命</span><span><b>{fateRolls}</b> 次重铸</span>
          </div>
          <div className="fate-grid">
            {options?.fate_system.slots.map((slot) => {
              const selected = fateSelections[slot.id];
              const candidates = fateCandidates[slot.id] || [];
              const answer = selected ? fateAnswers[selected.id] : undefined;
              return <article key={slot.id} className={`fate-slot ${selected ? `locked ${selected.rarity}` : ""}`}>
                <header><i>{slot.icon}</i><span><small>{String(slot.index).padStart(2, "0")}</small><strong>{slot.title}</strong><em>{slot.description}</em></span><button disabled={busy} onClick={() => rerollFate(slot)} title="AI 重铸本槽">↻</button></header>
                <div className="fate-candidates">{candidates.map((fate) => <button key={fate.id} className={`${fate.rarity} ${selected?.id === fate.id ? "selected" : ""}`} onClick={() => chooseFate(slot, fate)}>
                  <span className="fate-rarity">{fate.rarity === "red" ? "红" : fate.rarity === "gold" ? "金" : "蓝"}</span><strong>{fate.title}</strong><small>{fate.summary}</small>
                </button>)}</div>
                {selected?.rarity === "gold" && <div className="gold-inquiry">
                  <p>{selected.question}</p>
                  <div>{(["yes", "no", "custom"] as FateAnswer[]).map((kind) => <button key={kind} className={answer?.answer === kind ? "selected" : ""} onClick={() => updateFateAnswer(selected.id, kind)}>{kind === "yes" ? "是" : kind === "no" ? "否" : "自定义"}</button>)}</div>
                  {answer?.answer === "custom" && <textarea value={answer.custom} maxLength={600} onChange={(event) => updateFateAnswer(selected.id, "custom", event.target.value)} placeholder="描述你喜欢的具体表现、强度和变化……" />}
                </div>}
              </article>;
            })}
          </div>
        </>}
        {step === 2 && <>
          <span className="eyebrow">STEP 02</span><h1>先把关系和你想要的TA说清楚</h1><p>这段内容会直接交给 AI 生成本次命格候选，不经过固定词库。</p>
          <label>你们的关系<select value={(options?.relationships || []).includes(input.relationship) ? input.relationship : "__custom__"} onChange={(event) => setInput({ ...input, relationship: event.target.value === "__custom__" ? "" : event.target.value })}>{options?.relationships.map((item) => <option key={item}>{item}</option>)}<option value="__custom__">自定义关系</option></select></label>
          {!(options?.relationships || []).includes(input.relationship) && <label>自定义关系<input value={input.relationship} onChange={(event) => setInput({ ...input, relationship: event.target.value })} placeholder="例如：在同一座城市生活的伴侣" /></label>}
          <div className="two-column-fields">
            <label>用户名称<input value={input.user_name} onChange={(event) => setInput({ ...input, user_name: event.target.value })} /></label>
            <label>角色如何称呼你<input value={input.user_alias} onChange={(event) => setInput({ ...input, user_alias: event.target.value })} placeholder="可留空" /></label>
          </div>
          <label>你想要一个怎样的角色
            <textarea value={input.relationship_context} maxLength={2400} onChange={(event) => setInput({ ...input, relationship_context: event.target.value })} placeholder="例如：TA很依赖我，平时百依百顺，受到冷落会明显不安；或者TA控制欲很强，会直接命令我，也真的会发脾气……" />
          </label>
          <div className="relationship-seal-preview"><span>关系印章</span><strong>{input.relationship || "未定义"}</strong><small>{input.ai_name || "AI"} × {input.user_alias || input.user_name || "用户"}</small></div>
        </>}
        {step === 4 && <>
          <span className="eyebrow">STEP 04 · CONVERGENCE</span><h1>合相定格</h1>
          <p>十二条命格会在下一步统一合成为角色，不会以词条清单直接塞进对话。</p>
          <section className="fate-convergence">
            <div className="fate-core-seal"><span>{input.ai_name.slice(0, 1) || "命"}</span><strong>{input.ai_name || "待定角色"}</strong><small>{input.relationship} · {completedFates}/12命格</small></div>
            <div className="fate-orbit">{options?.fate_system.slots.map((slot) => {
              const fate = fateSelections[slot.id];
              return <div key={slot.id} className={fate?.rarity || "empty"}><i>{slot.icon}</i><span>{slot.short_title}</span><strong>{fate?.title || "未定"}</strong></div>;
            })}</div>
          </section>
          <div className="fate-convergence-notes"><p><b>红命保留代价</b>不会被AI洗成完美优点。</p><p><b>金命服从问心</b>是、否、自定义会成为不同生成方向。</p><p><b>蓝命建立生活</b>防止角色只剩戏剧冲突。</p></div>
        </>}
        {step === 5 && <>
          <span className="eyebrow">STEP 05 · PERSONA SYNTHESIS</span><h1>命格已成人设</h1>
          {busy && <div className="card-generating"><i /><strong>正在构筑角色</strong><small>API 不可用时会自动采用本地模板</small></div>}
          {!busy && draft && <div className="character-preview-card">
            <div className="preview-portrait"><img src={draft.avatar && "src" in draft.avatar ? text(draft.avatar.src) : placeholderAvatar} alt="" /><span>{draft.generation_mode === "llm" ? "AI 辅助生成" : "本地模板生成"}</span></div>
            <div><span className="eyebrow">{input.ai_gender} · {input.relationship}</span><h2>{text(identity.name) || input.ai_name}</h2><p>{text(identity.self_description)}</p><dl><div><dt>性格</dt><dd>{Array.isArray(personality.core_traits) ? personality.core_traits.join(" · ") : compiledInput().core_traits.join(" · ")}</dd></div><div><dt>缺陷</dt><dd>{compiledInput().flaw}</dd></div><div><dt>关系</dt><dd>{text(relationship.relationship_definition) || input.relationship}</dd></div></dl></div>
          </div>}
          {!busy && draft?.blueprint && <section className="blueprint-editor" aria-label="角色八板块编辑器">
            <header>
              <div><span className="eyebrow">7 DIMENSIONS · 8 BLOCKS</span><h2>角色的有效设定</h2></div>
              <strong className={draft.blueprint.quality.complete ? "quality-pass" : "quality-fail"}>
                {draft.blueprint.quality.effective_tokens} 有效 token
              </strong>
            </header>
            <p>勾选任意多个板块，再用自然语言告诉 AI 如何重写。未勾选板块和锁定事实不会改变。</p>
            <div className="blueprint-block-grid">
              {Object.entries(draft.blueprint.blocks).map(([blockId, block], index) => {
                const selected = selectedBlockIds.includes(blockId);
                return <article key={blockId} className={selected ? "selected" : ""}>
                  <label>
                    <input type="checkbox" checked={selected} onChange={() => toggleBlueprintBlock(blockId)} />
                    <span><b>{String(index + 1).padStart(2, "0")}</b><strong>{block.title}</strong></span>
                    <small>{draft.blueprint.quality.block_tokens[blockId] || 0} token</small>
                  </label>
                  <p>{block.content}</p>
                </article>;
              })}
            </div>
            <div className="blueprint-rewrite-box">
              <textarea
                value={rewriteInstruction}
                onChange={(event) => setRewriteInstruction(event.target.value)}
                placeholder="例如：让TA更克制但不是冷淡；冲突时先嘴硬，事后会用具体行动修复。"
                maxLength={1200}
              />
              <button disabled={busy || !selectedBlockIds.length || !rewriteInstruction.trim()} onClick={() => void rewriteSelectedBlocks()}>
                重写所选 {selectedBlockIds.length || 0} 个板块
              </button>
            </div>
          </section>}
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
          {step < 5 ? <button className="primary" disabled={busy} onClick={() => void next()}>{step === 2 && busy ? "AI 正在生成命格…" : step === 4 ? "生成完整人设" : "继续"}</button> : <>
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
  initialCharacterId,
  onBack,
  onRefresh,
  onChat,
  onDraw,
}: {
  characters: CharacterSummary[];
  initialCharacterId?: string;
  onBack: () => void;
  onRefresh: () => Promise<void>;
  onChat: (character: CharacterSummary) => void;
  onDraw: () => void;
}) {
  const [selectedId, setSelectedId] = useState(
    initialCharacterId && characters.some((item) => item.character_id === initialCharacterId)
      ? initialCharacterId
      : characters[0]?.character_id || "",
  );
  const [record, setRecord] = useState<CharacterRecord | null>(null);
  const [editText, setEditText] = useState("");
  const [history, setHistory] = useState<Array<{ version_id: string; revision: number; updated_at: string }>>([]);
  const [error, setError] = useState("");
  const importRef = useRef<HTMLInputElement | null>(null);

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
      <button onClick={onBack}>← 返回主页</button>
      <div><span className="eyebrow">PERSONA</span><h1>人设管理</h1><p>管理人物档案、运行状态和连续对话。</p></div>
      <div><button onClick={() => importRef.current?.click()}>导入卡包</button><button className="primary" onClick={onDraw}>＋ 进入命定系统</button><input ref={importRef} hidden type="file" accept=".mindspace-card" onChange={(event) => void importCard(event.target.files?.[0])} /></div>
    </header>
    <div className="library-layout">
      <nav className="card-shelf">
        {characters.map((character) => <button key={character.character_id} className={selectedId === character.character_id ? "active" : ""} onClick={() => setSelectedId(character.character_id)}>
          <img src={characterAvatar(character)} alt="" />
          <span><strong>{character.display_name}</strong><small>{character.relationship_label || "未定义关系"} · {character.latest_session_id ? "可继续对话" : "尚未开始"}</small></span>
          <i>{character.status === "archived" ? "已归档" : character.source === "draw" ? "灵感" : "自定义"}</i>
        </button>)}
        {!characters.length && <div className="library-empty"><b>还没有收藏角色</b><p>先抽取第一张卡，或导入 `.mindspace-card`。</p><button onClick={onDraw}>开始抽卡</button></div>}
      </nav>
      <section className="library-detail">
        {record ? <>
          <div className="library-hero">
            <img src={characterAvatar(record)} alt="" />
            <div><span>{record.gender} · {record.relationship_label}</span><h2>{record.display_name}</h2><p>{text(asRecord(record.ai_profile.identity).self_description)}</p><small>修订 {record.revision} · 更新于 {new Date(record.updated_at).toLocaleString()}</small></div>
          </div>
          <div className="library-actions">
            <button className="primary" onClick={() => onChat(record)}>{record.latest_session_id ? "继续对话" : "开始对话"}</button>
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
