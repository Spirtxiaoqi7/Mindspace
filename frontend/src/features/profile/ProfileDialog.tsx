import { useCallback, useEffect, useMemo, useState } from "react";

import { Modal } from "../../shared/Modal";
import { request } from "../../shared/api";
import { str } from "../../shared/formatters";
import type { ProfileHistoryItem, Role } from "../../types";
import { styledConfirm } from "../../ui/styledConfirm";
const PROFILE_FIELD_LABELS: Record<string, string> = {
  identity: "身份", preferred_name: "常用称呼", real_name: "真实姓名", gender: "第一认同性别", occupation: "职业", language: "语言",
  name: "角色名称", self_description: "角色自述", relationship_to_user: "与用户关系",
  communication_preferences: "交流偏好", preferred_tone: "偏好语气", response_length: "回复长度",
  explanation_depth: "解释深度", preferred_names: "喜欢的称呼", disliked_expressions: "不喜欢的表达",
  stable_preferences: "稳定偏好", likes: "喜欢", dislikes: "不喜欢", interests: "兴趣", habits: "习惯",
  background: "经历", important_experiences: "重要经历", behavior_requirements: "用户行为要求",
  personality: "角色性格", core_traits: "核心性格", speech_style: "表达风格",
  relationship_rules: "关系规则", relationship_definition: "关系定义", preferred_interactions: "偏好互动",
  conflict_behavior: "冲突处理", repair_behavior: "关系修复", behavior_rules: "角色行为规则",
  always_apply: "始终执行", contextual_rules: "情境规则", avoid: "避免行为", hard_boundaries: "硬性边界",
  continuity: "关系延续", important_shared_experiences: "共同经历", persistent_attitudes: "持续态度",
  long_term_goals: "长期目标", relationship_state: "当前关系", current_stage: "当前阶段",
  roleplay: "角色演绎", selfhood: "角色自我", values: "价值取向", personal_opinions: "个人看法",
  flaws: "缺点", contradictions: "内在矛盾", private_interests: "私人兴趣", personal_goals: "个人目标",
  agency: "自主性", initiative_sources: "主动话题来源", self_directed_choices: "自主选择方式",
  attention_triggers: "注意力触发", boredom_triggers: "厌倦触发", default_conflict_posture: "默认分歧立场",
  voice: "角色语言", cadence: "语言节奏", preferred_vocabulary: "常用词", disliked_phrases: "禁用套话",
  humor_style: "幽默方式", action_dialogue_balance: "动作与台词比例", scenario_baseline: "常态场景",
  post_history_note: "历史后角色校准", r18_protocol: "用户私有 R18 描写协议",
  examples: "分类对话示例", casual: "日常示例",
  disagreement: "分歧示例", initiative: "主动表达示例", scene_transition: "转场示例",
  intimate: "亲密互动示例", roleplay_state: "角色场景状态", scene: "当前场景",
  description: "角色基础信息", scenario: "关系与日常情境", first_mes: "首次开场",
  alternate_greetings: "备用开场", mes_example: "对话示例", memory: "长期记忆",
  appearance: "外表设定", height_cm: "身高（cm）", body_shape: "体型", body_features: "身体特征",
  face: "面部特征", hair: "发型发色", eyes: "眼睛", skin: "肤色与质感",
  distinguishing_features: "辨识特征", signature_outfit: "标志穿着", intimate_features: "亲密身体特征",
  preferences: "偏好记忆", tasks: "任务记忆", relationship: "关系类型",
  relationship_context: "关系补充", user_alias: "AI 对你的称呼",
  location: "地点", time_anchor: "时间锚点", character_outfit: "角色穿着",
  character_posture: "角色姿态", character_activity: "角色正在做的事", active_objects: "场景物件",
  open_threads: "未完互动", last_transition: "最近转场", updated_round: "更新轮次",
  agent_drive: "角色当前驱动力", current_intent: "角色当前意图", own_activity: "角色自身活动",
  unresolved_choice: "角色未决选择", initiative_type_history: "主动类型历史",
  current_tone: "当前氛围", recent_conflicts: "近期冲突", recent_positive_events: "近期积极事件",
  unresolved_issues: "未解决事项", user_state: "用户当前状态", current_goal: "当前目标",
  current_task: "当前任务", current_topic: "当前话题", temporary_preferences: "临时偏好",
  current_emotional_cues: "当前情绪线索", ai_state: "AI 当前状态", pending_responses: "待回应事项",
  current_intentions: "当前意图", session_state: "会话状态", session_summary: "会话摘要",
  open_questions: "开放问题", pending_actions: "待办事项", active_entities: "当前实体",
};
const PROFILE_TECHNICAL_FIELDS = new Set(["schema_version", "profile_type", "revision", "updated_at"]);

function ProfileFieldEditor({ fieldKey, value, path, onChange }: { fieldKey: string; value: unknown; path: string[]; onChange: (path: string[], value: unknown) => void }) {
  const label = PROFILE_FIELD_LABELS[fieldKey] || fieldKey;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return <fieldset className="profile-form-section"><legend>{label}</legend><div className="profile-form-grid">{Object.entries(value as Record<string, unknown>).filter(([key]) => !PROFILE_TECHNICAL_FIELDS.has(key)).map(([key, item]) => <ProfileFieldEditor key={`${path.join(".")}.${key}`} fieldKey={key} value={item} path={[...path, key]} onChange={onChange} />)}</div></fieldset>;
  }
  if (Array.isArray(value)) {
    const isExample = path.includes("examples");
    return <label className="profile-form-field profile-form-list"><span>{label}</span><textarea aria-label={label} value={value.map(String).join("\n")} placeholder={isExample ? "每行一例，例如：用户：…… → 角色：……" : "每行一项；留空表示暂无记录"} onChange={(event) => onChange(path, event.target.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean))} />{isExample && <small>只会按当前情境选取最多两条，不会整份塞入 Prompt。</small>}</label>;
  }
  if (typeof value === "boolean") {
    return <label className="profile-form-field profile-form-check"><input aria-label={label} type="checkbox" checked={value} onChange={(event) => onChange(path, event.target.checked)} /><span>{label}</span></label>;
  }
  if (fieldKey === "gender") {
    return <label className="profile-form-field"><span>{label}</span><select aria-label={label} value={String(value)} onChange={(event) => onChange(path, event.target.value)}><option value="男">男</option><option value="女">女</option><option value="不指定">不指定</option></select><small>用户手动保存后作为模型最高优先级身份；AI 不能自行改写。通用代词始终使用TA。</small></label>;
  }
  if (["description", "personality", "scenario", "first_mes", "mes_example", "relationship_context"].includes(fieldKey)) {
    return <label className="profile-form-field profile-form-list"><span>{label}</span><textarea aria-label={label} value={value == null ? "" : String(value)} onChange={(event) => onChange(path, event.target.value)} /></label>;
  }
  return <label className="profile-form-field"><span>{label}</span><input aria-label={label} type={typeof value === "number" ? "number" : "text"} value={value == null ? "" : String(value)} onChange={(event) => onChange(path, typeof value === "number" ? Number(event.target.value) : event.target.value)} /></label>;
}

function profileObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function profileStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item || "").trim()).filter(Boolean) : [];
}

function v2ProfileEditorDocument(record: Record<string, unknown>): Record<string, unknown> {
  const card = profileObject(record.card);
  const data = profileObject(card.data);
  const extensions = profileObject(data.extensions);
  const mindspace = profileObject(extensions.mindspace);
  const memory = profileObject(record.memory);
  const cardMemory = profileObject(data.memory);
  return {
    revision: Number(record.revision || 0),
    name: str(data.name),
    gender: str(mindspace.gender || record.gender || "不指定"),
    user_alias: str(record.user_alias || mindspace.user_alias),
    relationship: str(mindspace.relationship || record.relationship_label),
    relationship_context: str(mindspace.relationship_context),
    appearance: profileObject(mindspace.appearance),
    description: str(data.description),
    personality: str(data.personality),
    scenario: str(data.scenario),
    first_mes: str(data.first_mes),
    alternate_greetings: profileStringList(data.alternate_greetings),
    mes_example: str(data.mes_example),
    memory: {
      preferences: profileStringList(memory.preferences || cardMemory.preferences),
      tasks: profileStringList(memory.tasks || cardMemory.tasks),
    },
  };
}

export function ProfileDialog({ characterId, initialName, onClose, onDirty, onOpenConnection, onOpenMemory, onSaved, notify }: { characterId: string; initialName: Role | "state"; onClose: () => void; onDirty: (dirty: boolean) => void; onOpenConnection: () => void; onOpenMemory: () => void; onSaved: () => void; notify: (message: string) => void }) {
  const [name, setName] = useState(initialName); const [document, setDocument] = useState(""); const [savedDocument, setSavedDocument] = useState(""); const [history, setHistory] = useState<ProfileHistoryItem[]>([]); const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false); const [mode, setMode] = useState<"form" | "json">("form"); const [error, setError] = useState("");
  const [v2Card, setV2Card] = useState<Record<string, unknown> | null>(null);
  const [characterUsesV2, setCharacterUsesV2] = useState(false);
  const parsed = useMemo(() => { try { const value = JSON.parse(document); return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null; } catch { return null; } }, [document]);
  const characterQuery = name === "user" || !characterId ? "" : `?character_id=${encodeURIComponent(characterId)}`;
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      if (name === "assistant" && characterId) {
        const [record, versions] = await Promise.all([
          request<Record<string, unknown>>(`/api/v1/characters/${encodeURIComponent(characterId)}`),
          request<{ items: ProfileHistoryItem[] }>(`/api/v1/characters/${encodeURIComponent(characterId)}/history`).catch(() => ({ items: [] })),
        ]);
        const card = profileObject(record.card);
        if (Object.keys(card).length) {
          const serialized = JSON.stringify(v2ProfileEditorDocument(record), null, 2);
          setV2Card(card); setCharacterUsesV2(true); setDocument(serialized); setSavedDocument(serialized); setHistory(versions.items);
          return;
        }
        setV2Card(null); setCharacterUsesV2(false);
      } else {
        setV2Card(null);
      }
      const [value, versions] = await Promise.all([
        request<Record<string, unknown>>(`/api/v1/profiles/${name}${characterQuery}`),
        request<{ items: ProfileHistoryItem[] }>(`/api/v1/profiles/${name}/history${characterQuery}`).catch(() => ({ items: [] })),
      ]);
      const serialized = JSON.stringify(value, null, 2); setDocument(serialized); setSavedDocument(serialized); setHistory(versions.items);
    } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); }
    finally { setLoading(false); }
  }, [characterId, characterQuery, name, notify]);
  useEffect(() => { void load(); }, [load]); useEffect(() => { onDirty(document !== savedDocument); return () => onDirty(false); }, [document, onDirty, savedDocument]);
  const updateValue = useCallback((path: string[], value: unknown) => { if (!parsed) return; const next = structuredClone(parsed); let cursor: Record<string, unknown> = next; path.slice(0, -1).forEach((key) => { cursor = cursor[key] as Record<string, unknown>; }); cursor[path[path.length - 1]] = value; setDocument(JSON.stringify(next, null, 2)); setError(""); }, [parsed]);
  const save = async () => {
    if (!parsed) { setError("JSON 格式无效，请修正后再保存。"); return; }
    setSaving(true); setError("");
    try {
      if (v2Card && name === "assistant" && characterId) {
        const roleName = str(parsed.name).trim();
        if (!roleName) throw new Error("角色名称不能为空");
        const baseData = profileObject(v2Card.data);
        const baseExtensions = profileObject(baseData.extensions);
        const baseMindspace = profileObject(baseExtensions.mindspace);
        const parsedMemory = profileObject(parsed.memory);
        const memory = {
          preferences: profileStringList(parsedMemory.preferences),
          tasks: profileStringList(parsedMemory.tasks),
        };
        const relationship = str(parsed.relationship).trim();
        const userAlias = str(parsed.user_alias).trim();
        const appearance = profileObject(parsed.appearance);
        const nextCard = {
          ...v2Card,
          data: {
            ...baseData,
            name: roleName,
            description: str(parsed.description),
            personality: str(parsed.personality),
            scenario: str(parsed.scenario),
            first_mes: str(parsed.first_mes),
            alternate_greetings: profileStringList(parsed.alternate_greetings),
            mes_example: str(parsed.mes_example),
            memory,
            extensions: {
              ...baseExtensions,
              mindspace: {
                ...baseMindspace,
                gender: str(parsed.gender || "不指定"),
                relationship,
                relationship_context: str(parsed.relationship_context),
                user_alias: userAlias,
                appearance,
              },
            },
          },
        };
        const result = await request<{ character: Record<string, unknown> }>(`/api/v1/characters/${encodeURIComponent(characterId)}`, {
          method: "PUT",
          body: JSON.stringify({ revision: Number(parsed.revision || 0), card: nextCard, memory, user_alias: userAlias, relationship_label: relationship }),
        });
        const serialized = JSON.stringify(v2ProfileEditorDocument(result.character), null, 2);
        setV2Card(profileObject(result.character.card)); setDocument(serialized); setSavedDocument(serialized);
        const versions = await request<{ items: ProfileHistoryItem[] }>(`/api/v1/characters/${encodeURIComponent(characterId)}/history`).catch(() => ({ items: [] }));
        setHistory(versions.items); onSaved(); notify("V2 角色卡已保存，后续对话将使用新版本");
      } else {
        const payload = name === "user" ? {
          schema_version: "1.3.0",
          profile_type: "user",
          revision: Number(parsed.revision || 0),
          identity: {
            preferred_name: str(profileObject(parsed.identity).preferred_name).trim(),
            gender: str(profileObject(parsed.identity).gender || "不指定"),
          },
          custom_profile: str(parsed.custom_profile).trim(),
        } : parsed;
        if (name === "user" && !str(profileObject(payload.identity).preferred_name).trim()) throw new Error("用户名字不能为空");
        const result = await request<{ document: Record<string, unknown> }>(`/api/v1/profiles/${name}${characterQuery}`, { method: "PUT", body: JSON.stringify(payload) });
        const serialized = JSON.stringify(result.document, null, 2); setDocument(serialized); setSavedDocument(serialized); onSaved(); notify("档案已保存，人物名称与后续对话将使用新版本");
      }
    } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); }
    finally { setSaving(false); }
  };
  const restorePrevious = async () => {
    const previous = history[0]; if (!previous || !parsed) return;
    if (!(await styledConfirm({ title: `恢复修订 ${previous.revision}？`, message: "当前版本仍会保留在历史中，并会生成一个新的修订版本。", confirmLabel: "恢复版本" }))) return;
    setSaving(true); setError("");
    try {
      if (v2Card && name === "assistant" && characterId) {
        await request(`/api/v1/characters/${encodeURIComponent(characterId)}/restore`, { method: "POST", body: JSON.stringify({ version_id: previous.version_id, expected_revision: Number(parsed.revision || 0) }) });
      } else {
        await request(`/api/v1/profiles/${name}/restore${characterQuery}`, { method: "POST", body: JSON.stringify({ version_id: previous.version_id, expected_revision: parsed.revision }) });
      }
      notify("已恢复上一版本，并生成新的修订"); await load(); onSaved();
    } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); }
    finally { setSaving(false); }
  };
  const switchProfile = async (id: Role | "state") => { if (document !== savedDocument && !(await styledConfirm({ title: "放弃未保存的修改？", message: "切换档案后，本页尚未保存的编辑会丢失。", confirmLabel: "继续切换", danger: true }))) return; if (id === "user") setMode("form"); setName(id); };
  const openMemory = async () => { if (document !== savedDocument && !(await styledConfirm({ title: "先放弃未保存的修改？", message: "进入长期记忆后，本页尚未保存的输入会丢失。", confirmLabel: "进入长期记忆", danger: true }))) return; onOpenMemory(); };
  return <Modal
    title="人设工作区"
    kicker="PERSONA WORKSPACE"
    onClose={onClose}
    footer={<>
      <button className="secondary" disabled={loading || saving || !history.length} onClick={() => void restorePrevious()}>恢复上一版本</button>
      <button className="secondary" disabled={loading || saving} onClick={() => void load()}>放弃修改并重载</button>
      <button className="primary" disabled={loading || saving || !parsed || document === savedDocument} onClick={() => void save()}>{saving ? "正在保存…" : "保存档案"}</button>
    </>}
  >
    <div className="profile-tabs persona-workspace-tabs">
      {(characterUsesV2 ? [["user", "用户档案"], ["assistant", "V2 角色卡"]] : [["user", "用户档案"], ["assistant", "AI 档案"], ["state", "运行状态"]]).map(([id, label]) => <button className={name === id ? "active" : ""} key={id} onClick={() => switchProfile(id as Role | "state")}>{label}</button>)}
      <button className="profile-connection-tab" onClick={onOpenConnection}>API 连接 <span>↗</span></button>
    </div>
    <div className="profile-editor-toolbar">
      <p className="advanced-note">{name === "user" ? "这里只保存你的称呼、性别和手动补充资料；AI 与自动记忆不能改写。" : v2Card ? "这里直接编辑标准 chara_card_v2；名称、性别、关系、角色文本和偏好/任务记忆保存后立即进入后续对话。" : "用户修改直接生效并生成新 revision；AI 后续写回必须基于该 revision。"} 当前保留 {history.length} 个可恢复版本。</p>
      {name !== "user" && <div><button className={mode === "form" ? "active" : ""} onClick={() => setMode("form")}>表单编辑</button><button className={mode === "json" ? "active" : ""} onClick={() => setMode("json")}>高级 JSON</button></div>}
    </div>
    {error && <div className="profile-editor-error" role="alert">{error}</div>}
    {loading ? <div className="empty-mini">正在载入档案…</div> : name === "user" && parsed ? <div className="user-profile-compact">
      <section className="user-profile-card">
        <header><span>ABOUT YOU</span><h3>你的基础资料</h3><p>这些内容会作为稳定身份用于称呼、代词和角色互动。</p></header>
        <div className="user-profile-fields">
          <label><span>用户名字</span><input autoComplete="nickname" maxLength={80} value={str(profileObject(parsed.identity).preferred_name)} onChange={(event) => updateValue(["identity", "preferred_name"], event.target.value)} /></label>
          <label><span>用户性别</span><select value={str(profileObject(parsed.identity).gender || "不指定")} onChange={(event) => updateValue(["identity", "gender"], event.target.value)}><option value="男">男</option><option value="女">女</option><option value="不指定">不指定</option></select></label>
          <label className="user-profile-custom"><span>补充资料 <small>{str(parsed.custom_profile).length} / 500</small></span><textarea maxLength={500} value={str(parsed.custom_profile)} placeholder="可选：用自然语言填写职业、习惯、偏好或希望角色了解的稳定信息。" onChange={(event) => updateValue(["custom_profile"], event.target.value)} /></label>
        </div>
      </section>
      <button className="memory-jump-card" onClick={() => void openMemory()}>
        <span className="memory-jump-mark" aria-hidden="true">忆</span><span><strong>长期记忆</strong><small>查看和管理对话中形成的偏好、经历与重要事实</small></span><b aria-hidden="true">→</b>
      </button>
    </div> : mode === "json" ? <textarea aria-label="高级 JSON 编辑器" className="json-editor" value={document} onChange={(event) => { setDocument(event.target.value); setError(""); }} spellCheck={false} /> : parsed ? <div className="profile-form">{Object.entries(parsed).filter(([key]) => !PROFILE_TECHNICAL_FIELDS.has(key)).map(([key, value]) => <ProfileFieldEditor key={key} fieldKey={key} value={value} path={[key]} onChange={updateValue} />)}</div> : <div className="profile-editor-error" role="alert">JSON 格式无效，请切换到高级 JSON 修正。</div>}
  </Modal>;
}
