import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type CSSProperties,
  type DragEvent,
  type PointerEvent,
  type WheelEvent,
} from "react";
import { apiV1Request, HttpError } from "./shared/api";
import "./destiny-canvas.css";

type Willingness = "low" | "neutral" | "normal" | "high";
type ModelState =
  | "idle"
  | "archetypes"
  | "cards"
  | "archetypes_failed"
  | "cards_failed"
  | "synthesis"
  | "synthesis_failed"
  | "commit"
  | "commit_failed"
  | "chat"
  | "chat_failed";

type AvatarEntry = { src: string; aspect: string; scale: number; x: number; y: number };
type SeedForm = {
  ai_name: string;
  ai_gender: "女" | "男" | "不指定";
  user_name: string;
  user_alias: string;
  relationship: string;
  custom_relationship: string;
  relationship_context: string;
  character_expectation: string;
  appearance_expectation: string;
  adult_character: boolean;
  avatar: AvatarEntry | null;
};
type Archetype = { id: string; label: string; summary: string };
type Slot = { id: string; index: number; name: string; axis: string; icon: string; x: number; y: number };
type Card = {
  card_id: string;
  source_id: string;
  source_label: string;
  slot_id: string;
  slot_name: string;
  label: string;
  summary: string;
  interaction_willingness: Willingness;
};
type Journey = {
  journey_id: string;
  schema_version: string;
  revision: number;
  status: string;
  seed: Omit<SeedForm, "custom_relationship">;
  archetypes: Archetype[];
  cards_by_slot: Record<string, Card[]>;
  card_batches?: Record<string, { status?: "pending" | "generating" | "ready" | "failed"; slot_ids?: string[] }>;
  selections: Record<string, Card>;
  final_card: { data?: Record<string, unknown> } | null;
  character_id?: string;
  model_calls: { archetypes: number; cards: number; synthesis: number };
  progress?: { stage: string; current: number; total: number; percent: number; message: string };
  errors?: Array<{ stage: string; message: string }>;
  read_state?: { state?: string; action?: string; character_id?: string; message?: string };
};
type Definition = {
  slots: Slot[];
  interaction_willingness: Record<Willingness, { label: string; meaning: string }>;
};
type DestinyCanvasProps = {
  defaultUserName?: string;
  onBack?: () => void;
  onCancel?: () => void;
  onCommitted?: (character: any) => void | Promise<void>;
};

const RELATIONSHIPS = ["朋友", "恋人", "夫妻", "青梅竹马", "室友", "同事", "搭档", "陪伴者", "自定义"];
const RESUME_KEY = "mindspace.destiny.v7.active";
const PENDING_CHAT_KEY = "mindspace.destiny.v7.pending-chat";
const DEFAULT_AVATAR = "/assets/characters/placeholder-1.webp";
const ROTATION_STARTS = [0, 2, 4, 6];
const WILLINGNESS: Array<{ value: Willingness; label: string; short: string }> = [
  { value: "low", label: "互动意愿降低", short: "赤蚀" },
  { value: "neutral", label: "不影响互动意愿", short: "青常" },
  { value: "normal", label: "常规互动意愿", short: "蓝显" },
  { value: "high", label: "互动意愿高亢", short: "金命" },
];

function defaultSeed(userName = "用户"): SeedForm {
  return {
    ai_name: "",
    ai_gender: "不指定",
    user_name: userName,
    user_alias: "",
    relationship: "陪伴者",
    custom_relationship: "",
    relationship_context: "",
    character_expectation: "",
    appearance_expectation: "",
    adult_character: true,
    avatar: null,
  };
}

function willingnessLabel(value: Willingness): string {
  return WILLINGNESS.find((item) => item.value === value)?.label || value;
}

function countCards(journey: Journey | null): number {
  return Object.values(journey?.cards_by_slot || {}).reduce((total, cards) => total + cards.length, 0);
}

export function mergeUploadedAvatar(uploaded: AvatarEntry, current: AvatarEntry | null): AvatarEntry {
  return { ...uploaded, ...(current || {}), src: uploaded.src };
}

export default function DestinyCanvas({ defaultUserName, onBack, onCancel, onCommitted }: DestinyCanvasProps) {
  const [definition, setDefinition] = useState<Definition>({ slots: [], interaction_willingness: {} as Definition["interaction_willingness"] });
  const [journey, setJourney] = useState<Journey | null>(null);
  const [seed, setSeed] = useState<SeedForm>(() => defaultSeed(defaultUserName));
  const [seedOpen, setSeedOpen] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState<"directions" | "selections" | "card">("directions");
  const [completionOpen, setCompletionOpen] = useState(false);
  const [activeSlotId, setActiveSlotId] = useState("");
  const [stageOpen, setStageOpen] = useState(false);
  const [previewCardId, setPreviewCardId] = useState("");
  const [rotation, setRotation] = useState(0);
  const [modelState, setModelState] = useState<ModelState>("idle");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [fallbackStage, setFallbackStage] = useState<"" | "archetypes" | "cards">("");
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [localAvatarUrl, setLocalAvatarUrl] = useState("");
  const [avatarTools, setAvatarTools] = useState(false);
  const [camera, setCamera] = useState({ x: 0, y: 0, scale: 1 });
  const [dragging, setDragging] = useState<{ pointerId: number; x: number; y: number; cx: number; cy: number } | null>(null);
  const [dissolveArmed, setDissolveArmed] = useState(false);
  const [flying, setFlying] = useState(false);
  const [selectionEffectCardId, setSelectionEffectCardId] = useState("");
  const [savingSelection, setSavingSelection] = useState(false);
  const [arrivingSlotId, setArrivingSlotId] = useState("");
  const [committedCharacter, setCommittedCharacter] = useState<any>(null);
  const dissolveTimer = useRef<number | null>(null);
  const cameraMotionTimer = useRef<number | null>(null);
  const selectionEffectTimer = useRef<number | null>(null);
  const sceneRef = useRef<HTMLDivElement | null>(null);
  const worldCameraRef = useRef<HTMLDivElement | null>(null);

  const slots = definition.slots;
  const completedCount = Object.keys(journey?.selections || {}).length;
  const currentSlot = useMemo(
    () => slots.find((slot) => !journey?.selections?.[slot.id]) || (completedCount === 12 ? slots.at(-1) : slots[0]) || null,
    [completedCount, journey, slots],
  );
  const activeSlot = slots.find((slot) => slot.id === activeSlotId) || currentSlot;
  const activeCards = activeSlot ? journey?.cards_by_slot?.[activeSlot.id] || [] : [];
  const visibleCards = useMemo(() => {
    if (!activeCards.length) return [];
    const start = ROTATION_STARTS[rotation % ROTATION_STARTS.length];
    return [0, 1, 2].map((offset) => activeCards[(start + offset) % activeCards.length]).filter(Boolean);
  }, [activeCards, rotation]);
  const selectedCard = activeCards.find((card) => card.card_id === previewCardId)
    || journey?.selections?.[activeSlot?.id || ""]
    || null;
  const exit = onBack || onCancel;
  const archetypeCount = journey?.archetypes?.length || 0;
  const generatedCardCount = countCards(journey);
  const avatar = seed.avatar;
  const avatarSrc = localAvatarUrl || avatar?.src || DEFAULT_AVATAR;
  const cardData = (journey?.final_card?.data || {}) as Record<string, unknown>;
  const relationshipLabel = seed.relationship === "自定义" ? seed.custom_relationship : seed.relationship;

  useEffect(() => () => {
    if (cameraMotionTimer.current) window.clearTimeout(cameraMotionTimer.current);
    if (selectionEffectTimer.current) window.clearTimeout(selectionEffectTimer.current);
  }, []);

  function focusNode(slot: Slot) {
    const sceneRect = sceneRef.current?.getBoundingClientRect();
    const worldRect = worldCameraRef.current?.getBoundingClientRect();
    const nextScale = sceneRect && sceneRect.width < 760 ? 1.02 : 1.1;

    if (sceneRect && worldRect && sceneRect.width > 0 && worldRect.width > 0 && camera.scale > 0) {
      const worldWidth = worldRect.width / camera.scale;
      const worldHeight = worldRect.height / camera.scale;
      const worldCenterX = worldRect.left + worldRect.width / 2 - camera.x;
      const worldCenterY = worldRect.top + worldRect.height / 2 - camera.y;
      const nodeX = worldCenterX + (slot.x / 100 - 0.5) * worldWidth;
      const nodeY = worldCenterY + (slot.y / 100 - 0.5) * worldHeight;
      const targetX = sceneRect.left + sceneRect.width * (sceneRect.width < 760 ? 0.5 : 0.38);
      const targetY = sceneRect.top + sceneRect.height * 0.5;
      setCamera({
        scale: nextScale,
        x: Math.round(targetX - worldCenterX - nextScale * (nodeX - worldCenterX)),
        y: Math.round(targetY - worldCenterY - nextScale * (nodeY - worldCenterY)),
      });
    } else {
      setCamera({ x: 0, y: 0, scale: nextScale });
    }

    setArrivingSlotId(slot.id);
    if (cameraMotionTimer.current) window.clearTimeout(cameraMotionTimer.current);
    cameraMotionTimer.current = window.setTimeout(() => setArrivingSlotId(""), 840);
  }

  useEffect(() => {
    let cancelled = false;
    async function restore() {
      let loadedDefinition: Definition;
      try {
        loadedDefinition = await apiV1Request<Definition>("/destiny/definition");
        if (cancelled) return;
        setDefinition(loadedDefinition);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "命格定义载入失败");
        return;
      }
      try {
        const journeyId = window.localStorage.getItem(RESUME_KEY);
        const pendingRaw = window.localStorage.getItem(PENDING_CHAT_KEY);
        if (pendingRaw) {
          try {
            const pending = JSON.parse(pendingRaw);
            if (pending?.character_id) {
              setCommittedCharacter(pending);
              setSeed((current) => ({ ...current, ai_name: pending.display_name || current.ai_name }));
              setCompletionOpen(true);
              setModelState("chat_failed");
              setNotice("角色已收入角色库；本地聊天尚未建立，可以继续进入聊天。 ");
            }
          } catch {
            window.localStorage.removeItem(PENDING_CHAT_KEY);
          }
        }
        if (!journeyId) return;
        const saved = await apiV1Request<Journey>(`/destiny/journeys/${journeyId}`);
        if (cancelled) return;
        setJourney(saved);
        const savedSeed = saved.seed;
        const knownRelationship = RELATIONSHIPS.includes(savedSeed.relationship);
        setSeed({
          ...defaultSeed(defaultUserName),
          ...savedSeed,
          custom_relationship: knownRelationship ? "" : savedSeed.relationship,
          relationship: knownRelationship ? savedSeed.relationship : "自定义",
        });
        if (saved.status === "committed" || saved.character_id) {
          const characterId = saved.read_state?.character_id || saved.character_id;
          let recoveredCharacter: any = {
            character_id: characterId,
            display_name: saved.seed.ai_name,
            avatar: saved.seed.avatar,
          };
          if (characterId) {
            try {
              recoveredCharacter = await apiV1Request(`/characters/${characterId}`);
            } catch {
              // The server-side character_id remains enough to show recovery actions.
            }
          }
          setCommittedCharacter(recoveredCharacter);
          setSeedOpen(false);
          setCompletionOpen(true);
          setModelState("chat_failed");
          setNotice("角色已收入角色库；可以继续进入聊天，或前往角色库。 ");
          return;
        }
        if (saved.status === "archetypes_failed") { setModelState("archetypes_failed"); setFallbackStage("archetypes"); }
        if (saved.status === "cards_failed") {
          setModelState("cards_failed");
          setFallbackStage("cards");
          const lastError = (saved as Journey & { errors?: Array<{ message?: string }> }).errors?.at(-1)?.message;
          if (lastError) setError(lastError);
        }
        if (Object.keys(saved.cards_by_slot || {}).length && saved.status !== "cards_failed") {
          const next = loadedDefinition.slots.find((slot) => !saved.selections?.[slot.id]) || loadedDefinition.slots.at(-1);
          setSeedOpen(false);
          setActiveSlotId(next?.id || "");
          setStageOpen(Object.keys(saved.selections || {}).length < 12);
          if (Object.keys(saved.selections || {}).length === 12 && !saved.final_card && saved.status !== "committed") setCompletionOpen(true);
        }
      } catch (reason) {
        if (!cancelled) {
          // Only the journey resource can prove that this persisted journey no longer exists.
          if (reason instanceof HttpError && reason.status === 404) {
            window.localStorage.removeItem(RESUME_KEY);
          }
          setError(reason instanceof Error ? reason.message : "命格旅程恢复失败");
        }
      }
    }
    void restore();
    return () => { cancelled = true; };
  }, [defaultUserName]);

  useEffect(() => () => {
    if (localAvatarUrl) URL.revokeObjectURL(localAvatarUrl);
    if (dissolveTimer.current) window.clearTimeout(dissolveTimer.current);
  }, [localAvatarUrl]);

  function updateSeed<K extends keyof SeedForm>(key: K, value: SeedForm[K]) {
    setSeed((current) => ({ ...current, [key]: value }));
  }

  function seedPayload(nextAvatar: AvatarEntry | null) {
    return {
      ai_name: seed.ai_name,
      ai_gender: seed.ai_gender,
      user_name: seed.user_name,
      user_alias: seed.user_alias,
      relationship: relationshipLabel,
      relationship_context: seed.relationship_context,
      character_expectation: seed.character_expectation,
      appearance_expectation: seed.appearance_expectation,
      adult_character: seed.adult_character,
      avatar: nextAvatar || {},
    };
  }

  function validateSeed(): string {
    if (!seed.ai_name.trim()) return "请填写 AI 名称";
    if (!seed.user_name.trim()) return "请填写用户名称";
    if (seed.relationship === "自定义" && !seed.custom_relationship.trim()) return "请填写自定义关系";
    if (!seed.character_expectation.trim()) return "请写下一句话角色期待";
    return "";
  }

  async function uploadAvatar(): Promise<AvatarEntry | null> {
    if (!avatarFile) return seed.avatar;
    const form = new FormData();
    form.append("file", avatarFile);
    const response = await apiV1Request<{ avatar: AvatarEntry }>("/destiny/avatars", { method: "POST", body: form });
    const adjusted = mergeUploadedAvatar(response.avatar, seed.avatar);
    setSeed((current) => ({ ...current, avatar: adjusted }));
    setAvatarFile(null);
    return adjusted;
  }

  function isTemporaryDestinyAvatar(entry: AvatarEntry | null | undefined): boolean {
    return Boolean(entry?.src?.startsWith("/api/v1/avatar/files/destiny-upload-"));
  }

  async function discardUnattachedDestinyAvatar(entry: AvatarEntry | null | undefined) {
    if (journey || !isTemporaryDestinyAvatar(entry)) return;
    const filename = entry!.src.split("/").pop();
    if (!filename) return;
    try {
      await apiV1Request(`/destiny/avatars/${encodeURIComponent(filename)}`, { method: "DELETE" });
    } catch {
      // A network close must never erase the user's local seed edits. The server cleanup
      // removes a now-unreferenced temporary file on the next application startup.
    }
  }

  function isStaleJourneyRevision(reason: unknown): boolean {
    return reason instanceof HttpError
      && reason.status === 409
      && /stale destiny journey revision/i.test(reason.message);
  }

  async function recoverStaleJourney(current: Journey): Promise<Journey | null> {
    try {
      const recovered = await apiV1Request<Journey>(`/destiny/journeys/${current.journey_id}`);
      const generatedCards = Object.values(recovered.cards_by_slot || {}).reduce((total, cards) => total + cards.length, 0);
      setJourney(recovered);
      window.localStorage.setItem(RESUME_KEY, recovered.journey_id);
      setError("");

      if (recovered.status === "archetypes_generating") {
        setModelState("archetypes");
        setFallbackStage("");
        setSeedOpen(true);
        setStageOpen(false);
        setNotice("角色方向仍在生成，已同步服务器进度。完成后可继续下一步。");
      } else if (recovered.status === "cards_generating") {
        setModelState("cards");
        setFallbackStage("");
        setSeedOpen(true);
        setStageOpen(false);
        setNotice("命签仍在生成，已同步服务器进度。完成后会恢复到画布。");
      } else if (generatedCards === 96) {
        const next = slots.find((slot) => !recovered.selections?.[slot.id]) || slots[0];
        setModelState("idle");
        setFallbackStage("");
        setSeedOpen(false);
        setStageOpen(true);
        setActiveSlotId(next?.id || "");
        setPreviewCardId(recovered.selections?.[next?.id || ""]?.card_id || "");
        setNotice("检测到命格状态已更新，已恢复最新的 96 张命签与选择进度。");
      } else if (recovered.archetypes.length === 8) {
        setModelState("cards_failed");
        setFallbackStage("cards");
        setSeedOpen(true);
        setStageOpen(false);
        setNotice(`检测到命格状态已更新，8 个角色方向和 ${generatedCards}/96 张命签已保留，请继续生成命签。`);
      } else {
        setModelState("archetypes_failed");
        setFallbackStage("archetypes");
        setSeedOpen(true);
        setStageOpen(false);
        setNotice("检测到命格状态已更新，请重新生成角色方向。");
      }
      return recovered;
    } catch {
      return null;
    }
  }

  async function runCards(current: Journey, useDefault = false) {
    setModelState("cards");
    setError("");
    setFallbackStage("");
    try {
      const query = new URLSearchParams({ expected_revision: String(current.revision) });
      if (useDefault) query.set("use_default", "true");
      const finished = await apiV1Request<Journey>(`/destiny/journeys/${current.journey_id}/cards?${query.toString()}`, { method: "POST" });
      setJourney(finished);
      window.localStorage.setItem(RESUME_KEY, finished.journey_id);
      if (finished.status === "cards_failed") {
        const lastError = (finished as Journey & { errors?: Array<{ message?: string }> }).errors?.at(-1)?.message;
        setModelState("cards_failed");
        setFallbackStage("cards");
        setSeedOpen(true);
        setStageOpen(false);
        setError(lastError || "命签生成失败；成功批次已保留，请继续生成失败批次。");
        return;
      }
      const next = slots.find((slot) => !finished.selections?.[slot.id]) || slots[0];
      setActiveSlotId(next?.id || "");
      setPreviewCardId("");
      setRotation(0);
      setModelState("idle");
      setNotice(useDefault ? "模型生成失败，已载入按性别区分的默认 96 张命签。" : "96 张命签已显化，请从第一项开始选择。");
      window.setTimeout(() => {
        setSeedOpen(false);
        setStageOpen(true);
        if (next) focusNode(next);
      }, 420);
    } catch (reason) {
      if (isStaleJourneyRevision(reason) && await recoverStaleJourney(current)) return;
      let recovered: Journey | null = null;
      try {
        recovered = await apiV1Request<Journey>(`/destiny/journeys/${current.journey_id}`);
        setJourney(recovered);
      } catch {
        // Keep the last usable snapshot; the visible stage error remains actionable.
      }
      setModelState("cards_failed");
      setFallbackStage("cards");
      setSeedOpen(true);
      setStageOpen(false);
      const persistedError = (recovered as (Journey & { errors?: Array<{ message?: string }> }) | null)?.errors?.at(-1)?.message;
      setError(persistedError || (reason instanceof Error ? reason.message : "命签生成失败"));
    }
  }

  async function runArchetypes(current: Journey, useDefault = false) {
    setModelState("archetypes");
    setError("");
    setFallbackStage("");
    try {
      const query = new URLSearchParams({ expected_revision: String(current.revision) });
      if (useDefault) query.set("use_default", "true");
      const directions = await apiV1Request<Journey>(`/destiny/journeys/${current.journey_id}/archetypes?${query.toString()}`, { method: "POST" });
      setJourney(directions);
      await runCards(directions, useDefault);
    } catch (reason) {
      if (isStaleJourneyRevision(reason) && await recoverStaleJourney(current)) return;
      try {
        setJourney(await apiV1Request<Journey>(`/destiny/journeys/${current.journey_id}`));
      } catch {
        // Keep the last usable snapshot; the visible stage error remains actionable.
      }
      setModelState("archetypes_failed");
      setFallbackStage("archetypes");
      setError(reason instanceof Error ? reason.message : "角色方向生成失败");
    }
  }

  async function generateJourney() {
    const invalid = validateSeed();
    if (invalid) { setError(invalid); return; }
    if (journey?.status === "committed" || journey?.character_id) {
      setError("该旅程中的角色已经收入角色库，请新建角色。 ");
      return;
    }
    if (journey && (journey.archetypes.length || completedCount)
      && !window.confirm("修改种子会重新生成 8 个角色方向和 96 张命签，现有十二项选择将清空。")) return;
    setError("");
    setFallbackStage("");
    try {
      const nextAvatar = await uploadAvatar();
      const reusingJourney = Boolean(journey);
      const created = await apiV1Request<Journey>(reusingJourney
        ? `/destiny/journeys/${journey!.journey_id}/seed?expected_revision=${journey!.revision}`
        : "/destiny/journeys", {
        method: reusingJourney ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(seedPayload(nextAvatar)),
      });
      setJourney(created);
      setSeed((current) => ({ ...current, avatar: created.seed.avatar || current.avatar }));
      window.localStorage.setItem(RESUME_KEY, created.journey_id);
      await runArchetypes(created);
    } catch (reason) {
      if (journey && isStaleJourneyRevision(reason) && await recoverStaleJourney(journey)) return;
      setModelState("idle");
      setError(reason instanceof Error ? reason.message : "角色种子创建失败");
    }
  }

  async function useDefaultTemplate() {
    if (!journey || !fallbackStage) return;
    setError("");
    try {
      const latest = await apiV1Request<Journey>(`/destiny/journeys/${journey.journey_id}`);
      setJourney(latest);
      if (fallbackStage === "archetypes") await runArchetypes(latest, true);
      else await runCards(latest, true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取最新旅程失败");
    }
  }

  function returnToSeedEditing() {
    setModelState("idle"); setFallbackStage(""); setError("");
    setSeedOpen(true); setStageOpen(false); setCompletionOpen(false);
    setNotice("已返回角色种子；修改后会重置当前旅程，不会遗留旧的角色方向、命签或头像快照。");
  }

  async function rewindDirections() {
    if (!journey || !journey.archetypes.length || savingSelection) return;
    setSavingSelection(true); setError("");
    try {
      const updated = await apiV1Request<Journey>(`/destiny/journeys/${journey.journey_id}/rewind/archetypes?expected_revision=${journey.revision}`, { method: "POST" });
      setJourney(updated); setModelState("archetypes_failed"); setFallbackStage("archetypes");
      setSeedOpen(true); setStageOpen(false); setCompletionOpen(false); setDrawerOpen(false);
      setActiveSlotId(""); setPreviewCardId(""); setRotation(0);
      setNotice("已撤回 8 个角色方向和 96 张命签；种子仍保留，可以重新生成或使用默认模板。");
    } catch (reason) {
      if (isStaleJourneyRevision(reason) && await recoverStaleJourney(journey)) return;
      setError(reason instanceof Error ? reason.message : "回退角色方向失败");
    } finally {
      setSavingSelection(false);
    }
  }

  function selectNode(slot: Slot) {
    if (journeyMutationBusy) return;
    const slotIndex = slots.findIndex((item) => item.id === slot.id);
    const currentIndex = currentSlot ? slots.findIndex((item) => item.id === currentSlot.id) : 0;
    if (slotIndex > currentIndex && !journey?.selections?.[slot.id]) {
      setNotice("请先完成当前节点。");
      return;
    }
    setActiveSlotId(slot.id);
    setStageOpen(true);
    setRotation(0);
    setPreviewCardId(journey?.selections?.[slot.id]?.card_id || "");
    focusNode(slot);
  }

  async function contractCard(cardId = previewCardId) {
    if (!journey || !activeSlot || !cardId) { setNotice("先预选一张命签，再进行落契。"); return; }
    if (journeyMutationBusy) return;
    const card = activeCards.find((item) => item.card_id === cardId);
    if (!card) { setNotice("这张命签不属于当前节点。"); return; }
    const replacing = Boolean(journey.selections?.[activeSlot.id]);
    setError("");
    setSavingSelection(true);
    try {
      const updated = await apiV1Request<Journey>(`/destiny/journeys/${journey.journey_id}/selections/${activeSlot.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ card_id: card.card_id, expected_revision: journey.revision }),
      });
      const nextCount = Object.keys(updated.selections).length;
      setJourney(updated);
      setFlying(true);
      if (replacing) {
        setNotice(`已更新 ${activeSlot.axis}，其余 ${Math.max(0, nextCount - 1)} 项选择保持不变。`);
      } else {
        const next = slots[slots.findIndex((slot) => slot.id === activeSlot.id) + 1];
        setNotice(next ? `已定 ${nextCount}/12，命线已转入下一节点。` : "十二项已定，可以生成角色并开始聊天。" );
      }
      window.setTimeout(() => {
        setFlying(false);
        if (replacing) {
          setPreviewCardId(updated.selections[activeSlot.id]?.card_id || "");
          focusNode(activeSlot);
          return;
        }
        const next = slots[slots.findIndex((slot) => slot.id === activeSlot.id) + 1];
        if (next) {
          setActiveSlotId(next.id);
          setPreviewCardId(updated.selections[next.id]?.card_id || "");
          setRotation(0);
          setStageOpen(true);
          focusNode(next);
        } else {
          setStageOpen(false);
          setCompletionOpen(true);
        }
      }, 620);
    } catch (reason) {
      if (isStaleJourneyRevision(reason) && await recoverStaleJourney(journey)) return;
      setError(reason instanceof Error ? reason.message : "保存选择失败");
    } finally {
      setSavingSelection(false);
    }
  }

  async function dissolveSelections() {
    if (!journey || journeyMutationBusy) return;
    if (!dissolveArmed) {
      setDissolveArmed(true);
      setNotice("三秒内再次点击“再点确认”以解契十二项选择。");
      dissolveTimer.current = window.setTimeout(() => setDissolveArmed(false), 3000);
      return;
    }
    setSavingSelection(true);
    try {
      const updated = await apiV1Request<Journey>(`/destiny/journeys/${journey.journey_id}/selections?expected_revision=${journey.revision}`, { method: "DELETE" });
      setJourney(updated);
      setDissolveArmed(false);
      setActiveSlotId(slots[0]?.id || "");
      setPreviewCardId("");
      setStageOpen(true);
      setCompletionOpen(false);
      setNotice("十二项选择已清空，角色方向和 96 张命签仍被保留。");
    } catch (reason) {
      if (isStaleJourneyRevision(reason) && await recoverStaleJourney(journey)) return;
      setError(reason instanceof Error ? reason.message : "解契失败");
    } finally {
      setSavingSelection(false);
    }
  }

  async function stepBack() {
    if (!journey || journeyMutationBusy) return;
    if (completedCount === 0) {
      await rewindDirections();
      return;
    }
    const previous = [...slots].reverse().find((slot) => journey.selections?.[slot.id]);
    if (!previous) return;
    setSavingSelection(true); setError("");
    try {
      const updated = await apiV1Request<Journey>(`/destiny/journeys/${journey.journey_id}/selections/${previous.id}?expected_revision=${journey.revision}`, { method: "DELETE" });
      setJourney(updated); setCompletionOpen(false); setDrawerOpen(false); setActiveSlotId(previous.id);
      setPreviewCardId(""); setRotation(0); setStageOpen(true);
      setNotice(`已回退到 ${previous.axis}，请重新选择。`); focusNode(previous);
    } catch (reason) {
      if (isStaleJourneyRevision(reason) && await recoverStaleJourney(journey)) return;
      setError(reason instanceof Error ? reason.message : "回退上一步失败");
    } finally {
      setSavingSelection(false);
    }
  }

  async function synthesize(current = journey): Promise<Journey | null> {
    if (!current || journeyMutationBusy || Object.keys(current.selections || {}).length !== 12) return null;
    if (current.final_card) return current;
    setModelState("synthesis");
    setError("");
    try {
      const updated = await apiV1Request<Journey>(`/destiny/journeys/${current.journey_id}/synthesize?expected_revision=${current.revision}`, { method: "POST" });
      setJourney(updated);
      setDrawerTab("card");
      setModelState("idle");
      return updated;
    } catch (reason) {
      const recovered = isStaleJourneyRevision(reason) ? await recoverStaleJourney(current) : null;
      if (recovered) return recovered.final_card ? recovered : null;
      setModelState("synthesis_failed");
      setError(reason instanceof Error ? reason.message : "V2 合成失败");
      return null;
    }
  }

  async function commitAndEnter(current: Journey) {
    setModelState("commit");
    setError("");
    let character: any;
    try {
      const response = await apiV1Request<{ success: boolean; character: any }>(`/destiny/journeys/${current.journey_id}/commit?expected_revision=${current.revision}`, { method: "POST" });
      character = response.character;
      setCommittedCharacter(character);
      window.localStorage.setItem(PENDING_CHAT_KEY, JSON.stringify(character));
      setJourney((saved) => saved ? { ...saved, status: "committed", character_id: character?.character_id } : saved);
    } catch (reason) {
      if (isStaleJourneyRevision(reason) && await recoverStaleJourney(current)) return;
      setModelState("commit_failed");
      setError(reason instanceof Error ? reason.message : "角色入库失败");
      return;
    }
    setModelState("chat");
    try {
      await onCommitted?.(character);
      window.localStorage.removeItem(PENDING_CHAT_KEY);
      window.localStorage.removeItem(RESUME_KEY);
      setModelState("idle");
      setNotice("角色已收入角色库并进入本地聊天。");
    } catch (reason) {
      setModelState("chat_failed");
      setError(reason instanceof Error ? reason.message : "本地会话创建失败");
    }
  }

  async function finishCharacter() {
    if (!journey || completedCount !== 12) return;
    setCompletionOpen(true);
    const ready = await synthesize(journey);
    if (ready) await commitAndEnter(ready);
  }

  async function retryChat() {
    if (!committedCharacter) {
      if (journey?.final_card) await commitAndEnter(journey);
      return;
    }
    setModelState("chat");
    setError("");
    try {
      await onCommitted?.(committedCharacter);
      window.localStorage.removeItem(PENDING_CHAT_KEY);
      window.localStorage.removeItem(RESUME_KEY);
      setModelState("idle");
    } catch (reason) {
      setModelState("chat_failed");
      setError(reason instanceof Error ? reason.message : "本地会话创建失败");
    }
  }

  function onAvatarChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const supportedName = /\.(png|jpe?g|jfif|webp|gif)$/i.test(file.name);
    const supportedType = /image\/(png|jpeg|webp|gif)/i.test(file.type);
    if (!supportedName && !supportedType) {
      setError("头像格式不受支持。请上传 PNG、JPEG、WebP 或 GIF 图片。");
      event.currentTarget.value = "";
      return;
    }
    if (!file.size) {
      setError("头像文件为空，请重新选择图片。");
      event.currentTarget.value = "";
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setError("头像文件超过 5 MiB，请压缩后再上传。");
      event.currentTarget.value = "";
      return;
    }
    if (localAvatarUrl) URL.revokeObjectURL(localAvatarUrl);
    const preview = URL.createObjectURL(file);
    setLocalAvatarUrl(preview);
    setAvatarFile(file);
    setSeed((current) => ({
      ...current,
      avatar: {
        ...(current.avatar || { aspect: "2 / 3", scale: 1, x: 0, y: 0 }),
        src: preview,
      },
    }));
    event.currentTarget.value = "";
    setError("");
  }

  async function restoreDefaultAvatar() {
    const previous = seed.avatar;
    if (localAvatarUrl) URL.revokeObjectURL(localAvatarUrl);
    setLocalAvatarUrl("");
    setAvatarFile(null);
    updateSeed("avatar", null);
    await discardUnattachedDestinyAvatar(previous);
  }

  function zoom(delta: number) {
    setCamera((current) => ({ ...current, scale: Math.max(0.55, Math.min(1.65, current.scale + delta)) }));
  }

  function onWheel(event: WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const direction = event.deltaY > 0 ? -0.1 : 0.1;
    const bounds = worldCameraRef.current?.getBoundingClientRect();
    setCamera((current) => {
      const nextScale = Math.max(0.55, Math.min(1.65, current.scale + direction));
      if (!bounds || nextScale === current.scale) return { ...current, scale: nextScale };
      const pointerX = event.clientX - (bounds.left + bounds.width / 2);
      const pointerY = event.clientY - (bounds.top + bounds.height / 2);
      const ratio = nextScale / current.scale;
      return {
        ...current,
        scale: nextScale,
        x: current.x + pointerX - ratio * pointerX,
        y: current.y + pointerY - ratio * pointerY,
      };
    });
  }

  function pointerDown(event: PointerEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest("[data-no-pan]")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging({ pointerId: event.pointerId, x: event.clientX, y: event.clientY, cx: camera.x, cy: camera.y });
  }

  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    if (dragging?.pointerId !== event.pointerId) return;
    setCamera((current) => ({ ...current, x: dragging.cx + event.clientX - dragging.x, y: dragging.cy + event.clientY - dragging.y }));
  }

  function pointerUp(event: PointerEvent<HTMLDivElement>) {
    if (dragging?.pointerId === event.pointerId) setDragging(null);
  }

  function dragStart(event: DragEvent<HTMLElement>, card: Card) {
    event.dataTransfer.setData("text/plain", card.card_id);
    chooseCard(card.card_id);
  }

  function chooseCard(cardId: string) {
    setPreviewCardId(cardId);
    if (selectionEffectTimer.current) window.clearTimeout(selectionEffectTimer.current);
    setSelectionEffectCardId(cardId);
    selectionEffectTimer.current = window.setTimeout(() => setSelectionEffectCardId(""), 560);
  }

  function dropToSeal(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    const cardId = event.dataTransfer.getData("text/plain");
    if (cardId) {
      chooseCard(cardId);
      void contractCard(cardId);
    }
  }

  async function closeSeed() {
    if (journey && generatedCardCount === 96) setSeedOpen(false);
    else {
      await discardUnattachedDestinyAvatar(seed.avatar);
      exit?.();
    }
  }

  const seedButtonLabel = modelState === "archetypes"
    ? "正在创作 8 个角色方向…"
    : modelState === "cards"
      ? "正在拆分 96 张命签…"
      : modelState === "archetypes_failed"
        ? "重新生成角色方向"
          : modelState === "cards_failed"
          ? `继续生成命签（${generatedCardCount}/96）`
          : "生成命格图";
  const completionButtonLabel = modelState === "synthesis"
    ? "正在合成 V2 角色卡…"
    : modelState === "commit"
      ? "正在收入角色库…"
      : modelState === "chat"
        ? "正在进入本地聊天…"
        : modelState === "synthesis_failed"
          ? "重试合成"
          : modelState === "commit_failed"
            ? "重试收入角色库"
            : modelState === "chat_failed"
              ? "开始聊天"
              : journey?.final_card
                ? "收入角色库并开始聊天"
                : "生成角色并开始聊天";
  const completionBusy = ["synthesis", "commit", "chat"].includes(modelState);
  const journeyMutationBusy = savingSelection || ["archetypes", "cards", "synthesis", "commit", "chat"].includes(modelState);

  return <main className="destiny-v7" aria-label="命格画布">
    <div className="paper-atmosphere" aria-hidden="true"><i /><i /><i /><i /></div>

    <header className="atlas-topbar" data-no-pan>
      <div className="atlas-brand">
        <button className="atlas-seal" type="button" onClick={() => { if (window.confirm("当前进度已自动保存。确认后返回角色入口？")) exit?.(); }} aria-label="返回角色入口">命</button>
        <span><b>十二命格星图</b><small>沿命线逐宿召签</small></span>
      </div>

      <section className={`progress-ribbon ${completedCount === 12 ? "is-complete" : ""}`} aria-label={`命格进度 ${completedCount}/12`} aria-live="polite">
        <span className="progress-kicker">当前命序</span>
        <span className="progress-number"><b>{String(currentSlot?.index || 1).padStart(2, "0")}</b><i>/12</i></span>
        <strong className="progress-name">{completedCount === 12 ? "命盘已成" : currentSlot?.axis || "候签"}</strong>
        <span className="progress-dots" aria-hidden="true">
          {slots.map((slot, index) => <i key={slot.id} className={index < completedCount ? "done" : index === completedCount && completedCount < 12 ? "current" : ""} />)}
        </span>
      </section>

      <div className="atlas-actions">
        <span>已定 <b>{completedCount}</b> / 12</span>
        <button type="button" disabled={(!completedCount && !archetypeCount) || journeyMutationBusy} onClick={() => void stepBack()}>上一步</button>
        <button type="button" disabled={!journey || journeyMutationBusy} onClick={() => void dissolveSelections()}>{dissolveArmed ? "再点确认" : "解契"}</button>
      </div>
    </header>

    <button className="seed-capsule" type="button" data-no-pan onClick={() => setSeedOpen(true)}>
      <span className="seed-mini-seal">生</span>
      <span><b>{seed.ai_name || "角色种子"}</b><small>{seed.ai_gender} · {relationshipLabel || "陪伴者"} · {completedCount}/12</small></span>
    </button>

    <button className={`book-orb ${completedCount === 12 ? "is-complete" : ""}`} type="button" data-no-pan onClick={() => setDrawerOpen(true)} aria-label="打开签册">
      <span>{ }</span><b>签册</b>{completedCount === 12 && <i>成</i>}
    </button>

    <div ref={sceneRef} className={`destiny-scene ${dragging ? "is-dragging" : ""} ${stageOpen ? "has-stage" : ""} ${arrivingSlotId ? "is-navigating" : ""}`} onWheel={onWheel} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}>
      <div className="world-wash wash-one" /><div className="world-wash wash-two" />
      <div ref={worldCameraRef} className="world-camera" style={{ transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.scale})` }}>
        <svg className="fate-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <path className="fate-ghost" d={slots.length ? `M ${slots.map((slot) => `${slot.x} ${slot.y}`).join(" L ")}` : ""} />
          {slots.slice(0, -1).map((slot, index) => {
            const next = slots[index + 1];
            return <line key={slot.id} x1={slot.x} y1={slot.y} x2={next.x} y2={next.y} className={index < completedCount ? "is-done" : index === completedCount ? "is-current" : ""} />;
          })}
          <path className="fate-orbit" d="M10 62 C27 24, 62 20, 91 68 C72 89, 35 92, 10 62Z" />
          <path className="fate-orbit thin" d="M18 34 C42 8, 79 28, 83 63 C61 85, 29 76, 18 34Z" />
        </svg>

        <div className="world-inscription inscription-a">观法</div>
        <div className="world-inscription inscription-b">言声</div>
        <div className="world-inscription inscription-c">心候</div>

        {slots.map((slot, index) => {
          const saved = journey?.selections?.[slot.id];
          const isActive = slot.id === activeSlot?.id && stageOpen;
          const future = !saved && index > completedCount;
          return <button
            key={slot.id}
            type="button"
            data-no-pan
            className={`fate-node node-${(index % 6) + 1} ${isActive ? "is-active" : ""} ${saved ? "is-set" : ""} ${future ? "is-future" : ""} ${arrivingSlotId === slot.id ? "is-arriving" : ""}`}
            style={{ left: `${slot.x}%`, top: `${slot.y}%` }}
            onClick={() => selectNode(slot)}
            aria-label={`${slot.axis}${saved ? "，已落契" : ""}`}
            aria-disabled={future}
            disabled={future || journeyMutationBusy}
          >
            <span className="node-index">{String(slot.index).padStart(2, "0")}</span>
            <span className="node-sigil"><b>{slot.icon}</b><i /><i /></span>
            <span className="node-label"><b>{slot.name}</b><small>{slot.axis}</small></span>
            {saved && <span className="node-choice">{saved.label}</span>}
            {saved && <span className="node-complete">定</span>}
          </button>;
        })}
      </div>

      {!stageOpen && generatedCardCount === 96 && completedCount < 12 && <button className="return-current" type="button" data-no-pan onClick={() => { if (currentSlot) { setActiveSlotId(currentSlot.id); setStageOpen(true); focusNode(currentSlot); } }}>
        <small>当前命序</small><b>回到 {currentSlot?.axis}</b>
      </button>}

      {stageOpen && activeSlot && journey && generatedCardCount === 96 && <section className="focus-stage" data-no-pan aria-label={`${activeSlot.axis}选择舞台`}>
        <button type="button" className="stage-close" onClick={() => setStageOpen(false)} aria-label="收起命签舞台">×</button>
        <header className="stage-heading">
          <span><i>{String(activeSlot.index).padStart(2, "0")}</i> · 性格分类</span>
          <h1>{activeSlot.axis}</h1>
          <b>{activeSlot.name}</b>
          <p>选择一项聊天中可见的特征。预选不会保存，落契后才写入旅程。</p>
        </header>

        <div className="willingness-legend" aria-label="互动意愿图例">
          {WILLINGNESS.map((item) => <span key={item.value} className={`legend-${item.value}`}><i /><b>{item.short}</b><small>{item.label}</small></span>)}
        </div>

        <div className="draw-status"><i /><span>{selectedCard ? "已择一签 · 待落契" : "三签已显 · 从中择一"}</span><i /></div>
        <div className={`slip-fan ${selectedCard ? "has-selection" : ""}`}>
          {visibleCards.map((card, index) => <button
            key={card.card_id}
            type="button"
            draggable
            onDragStart={(event) => dragStart(event, card)}
            className={`destiny-slip willingness-${card.interaction_willingness} ${previewCardId === card.card_id ? "is-preview" : ""} ${selectionEffectCardId === card.card_id ? "is-selecting" : ""} ${flying && previewCardId === card.card_id ? "is-flying" : ""} fan-${index}`}
            onClick={() => chooseCard(card.card_id)}
            aria-pressed={previewCardId === card.card_id}
          >
            <span className="slip-grain" />
            <span className="selection-burst" aria-hidden="true" />
            <header><span>源自 · {card.source_label}</span><b>{willingnessLabel(card.interaction_willingness)}</b></header>
            <div className="slip-mark">{card.interaction_willingness === "high" ? "金" : card.interaction_willingness === "low" ? "赤" : card.interaction_willingness === "neutral" ? "青" : "蓝"}</div>
            <h2>{card.label}</h2>
            <p>{card.summary}</p>
            <footer><span>{activeSlot.axis}</span><i>◇</i></footer>
          </button>)}
        </div>

        <button className="recast-dial" type="button" onClick={() => { setRotation((value) => (value + 1) % ROTATION_STARTS.length); setPreviewCardId(""); }} aria-label="轮换另外三张命签">
          <b>转</b><span>换一轮</span>
        </button>

        <section className={`selection-note ${selectedCard ? "has-choice" : ""}`}>
          {selectedCard ? <>
            <small>预选 · 源自 {selectedCard.source_label}</small>
            <h3>{selectedCard.label}</h3>
            <p>{selectedCard.summary}</p>
            <span className={`choice-willingness willingness-${selectedCard.interaction_willingness}`}>{willingnessLabel(selectedCard.interaction_willingness)}：{definition.interaction_willingness?.[selectedCard.interaction_willingness]?.meaning || "该特征对主动聊天和回应意愿的影响。"}</span>
          </> : <><small>尚未择签</small><h3>从三枚命签中择一</h3><p>“转”只轮换已经生成的八张命签，不会再次调用模型。</p></>}
        </section>

        <button
          className={`contract-seal ${selectedCard ? "is-ready" : ""}`}
          type="button"
          disabled={!selectedCard || flying || savingSelection}
          aria-busy={savingSelection}
          onClick={() => void contractCard()}
          onDragOver={(event) => { if (selectedCard) event.preventDefault(); }}
          onDrop={dropToSeal}
          aria-label={journey.selections?.[activeSlot.id] ? "改契此签" : "落契此签"}
        ><b>契</b><span>{journey.selections?.[activeSlot.id] ? "改契此签" : "落契此签"}</span></button>
      </section>}

      <nav className="canvas-controls" data-no-pan aria-label="画布控制">
        <button type="button" onClick={() => zoom(0.12)} aria-label="放大画布">＋</button>
        <button type="button" onClick={() => { setCamera({ x: 0, y: 0, scale: 1 }); setStageOpen(false); }} aria-label="适合画布">⌗</button>
        <span>{Math.round(camera.scale * 100)}%</span>
        <button type="button" onClick={() => { if (currentSlot) { setActiveSlotId(currentSlot.id); setStageOpen(true); focusNode(currentSlot); } }} aria-label="回到选中节点">◎</button>
        <button type="button" onClick={() => zoom(-0.12)} aria-label="缩小画布">−</button>
      </nav>
    </div>

    {(notice || (error && !seedOpen && !completionOpen)) && <div className={`destiny-toast ${error ? "is-error" : ""}`} role={error ? "alert" : "status"} data-no-pan>
      <span>{error || notice}</span><button type="button" onClick={() => { setError(""); setNotice(""); }} aria-label="关闭提示">×</button>
    </div>}

    {seedOpen && <div className="seed-gate" data-no-pan role="dialog" aria-modal="true" aria-label="角色种子">
      <section className="seed-panel">
        <button className="panel-close" type="button" onClick={closeSeed} aria-label="关闭角色种子">×</button>
        <header><span>CHARACTER SEED</span><h1>为这段关系留下一颗种子</h1><p>名称、头像和关系由你决定。模型只负责创作角色方向与聊天中可见的特征。</p></header>
        <div className="seed-content">
          <section className="avatar-editor">
            <div className="avatar-frame" style={{ "--avatar-scale": avatar?.scale || 1, "--avatar-x": `${avatar?.x || 0}%`, "--avatar-y": `${avatar?.y || 0}%` } as CSSProperties}>
              <img src={avatarSrc} alt="AI 头像预览" />
              <span>{seed.ai_name || "角色头像"}</span>
            </div>
            <label className="avatar-upload">上传头像<input type="file" accept="image/png,image/jpeg,image/webp,image/gif,.png,.jpg,.jpeg,.jfif,.webp,.gif" onChange={onAvatarChange} /></label>
            <div className="avatar-actions"><button type="button" onClick={() => setAvatarTools((value) => !value)}>调整头像</button><button type="button" onClick={() => void restoreDefaultAvatar()}>恢复默认</button></div>
            {avatarTools && <div className="avatar-tools">
              <label>缩放<input aria-label="头像缩放" type="range" min="0.8" max="2" step="0.05" value={avatar?.scale || 1} onChange={(event) => updateSeed("avatar", { ...(avatar || { src: avatarSrc, aspect: "2 / 3", x: 0, y: 0, scale: 1 }), scale: Number(event.target.value) })} /></label>
              <label>横移<input aria-label="头像横移" type="range" min="-40" max="40" value={avatar?.x || 0} onChange={(event) => updateSeed("avatar", { ...(avatar || { src: avatarSrc, aspect: "2 / 3", x: 0, y: 0, scale: 1 }), x: Number(event.target.value) })} /></label>
              <label>纵移<input aria-label="头像纵移" type="range" min="-40" max="40" value={avatar?.y || 0} onChange={(event) => updateSeed("avatar", { ...(avatar || { src: avatarSrc, aspect: "2 / 3", x: 0, y: 0, scale: 1 }), y: Number(event.target.value) })} /></label>
            </div>}
          </section>

          <section className="seed-fields">
            <div className="form-row"><label>AI 名称<input aria-label="AI 名称" value={seed.ai_name} maxLength={80} onChange={(event) => updateSeed("ai_name", event.target.value)} placeholder="例如：知夏" /></label><label>性别<select aria-label="性别" value={seed.ai_gender} onChange={(event) => updateSeed("ai_gender", event.target.value as SeedForm["ai_gender"])}><option value="女">女性</option><option value="男">男性</option><option value="不指定">不指定</option></select></label></div>
            <div className="form-row"><label>用户名称<input aria-label="用户名称" value={seed.user_name} maxLength={80} onChange={(event) => updateSeed("user_name", event.target.value)} /></label><label>AI 对你的称呼<input aria-label="AI 对你的称呼" value={seed.user_alias} maxLength={80} onChange={(event) => updateSeed("user_alias", event.target.value)} placeholder="为空时使用用户名称" /></label></div>
            <div className="form-row"><label>关系类型<select aria-label="关系类型" value={seed.relationship} onChange={(event) => updateSeed("relationship", event.target.value)}>{RELATIONSHIPS.map((item) => <option key={item}>{item}</option>)}</select></label>{seed.relationship === "自定义" && <label>自定义关系<input aria-label="自定义关系" value={seed.custom_relationship} maxLength={100} onChange={(event) => updateSeed("custom_relationship", event.target.value)} /></label>}</div>
            <label>关系补充<textarea aria-label="关系补充" value={seed.relationship_context} maxLength={2400} onChange={(event) => updateSeed("relationship_context", event.target.value)} placeholder="可选：说明当前关系状态和相处背景" /></label>
            <label>角色期待<textarea aria-label="角色期待" value={seed.character_expectation} required maxLength={2400} onChange={(event) => updateSeed("character_expectation", event.target.value)} placeholder="必填：希望对方是什么样、怎样相处" /></label>
            <label>外表期待<textarea aria-label="外表期待" value={seed.appearance_expectation} maxLength={1200} onChange={(event) => updateSeed("appearance_expectation", event.target.value)} placeholder="可选：例如 175cm、高挑丰腴、黑色长发；未填写时由角色卡自然补全" /></label>
            <label className="adult-toggle"><input type="checkbox" checked={seed.adult_character} onChange={(event) => updateSeed("adult_character", event.target.checked)} /><b>成年角色</b><span>关闭后不会生成成人亲密方向。</span></label>
          </section>
        </div>

        <section className="generation-progress" aria-label="命格生成进度" aria-live="polite">
          <div className={archetypeCount === 8 ? "is-done" : modelState === "archetypes" ? "is-active" : modelState === "archetypes_failed" ? "is-failed" : ""}><i>01</i><span><b>创建角色方向</b><small>{archetypeCount}/8</small></span></div>
          <em />
          <div className={generatedCardCount === 96 ? "is-done" : modelState === "cards" ? "is-active" : modelState === "cards_failed" ? "is-failed" : ""}><i>02</i><span><b>拆分轻量命签</b><small>{generatedCardCount}/96</small></span></div>
          <em />
          <div className={generatedCardCount === 96 ? "is-done" : ""}><i>03</i><span><b>进入十二命格</b><small>{generatedCardCount === 96 ? "已就绪" : "等待"}</small></span></div>
        </section>

        {error && seedOpen && !completionOpen && <div className="completion-error" role="alert">{error}</div>}
        <footer className="seed-footer"><p>调用失败时可重试，也可直接使用按角色性别区分的本地默认模板。</p>{modelState === "cards_failed" && journey?.archetypes.length ? <button className="seed-submit" type="button" disabled={journeyMutationBusy} onClick={() => void rewindDirections()}>重做角色方向</button> : modelState === "archetypes_failed" ? <button className="seed-submit" type="button" disabled={journeyMutationBusy} onClick={returnToSeedEditing}>修改角色种子</button> : null}{fallbackStage && <button className="seed-submit" type="button" disabled={journeyMutationBusy} onClick={() => void useDefaultTemplate()}>使用默认模板</button>}<button className="seed-submit" type="button" disabled={journeyMutationBusy || definition.slots.length !== 12} onClick={() => { if (modelState === "cards_failed" && journey) void runCards(journey); else if (modelState === "archetypes_failed" && journey) void runArchetypes(journey); else void generateJourney(); }}>{definition.slots.length !== 12 ? "正在载入命格定义…" : seedButtonLabel}</button></footer>
      </section>
    </div>}

    {drawerOpen && <><div className="drawer-shade" onClick={() => setDrawerOpen(false)} /><aside className="book-drawer" data-no-pan aria-label="签册">
      <header><div><span>DESTINY BOOK</span><h2>签册 · {seed.ai_name || "角色"}</h2></div><button type="button" onClick={() => setDrawerOpen(false)} aria-label="关闭签册">×</button></header>
      <nav>{([ ["directions", "八个方向"], ["selections", "已选命签"], ["card", "V2 角色卡"] ] as const).map(([tab, label]) => <button key={tab} type="button" className={drawerTab === tab ? "is-active" : ""} disabled={tab === "card" && !journey?.final_card} onClick={() => setDrawerTab(tab)}>{label}</button>)}</nav>
      <div className="drawer-scroll">
        {drawerTab === "directions" && <div className="direction-list">{journey?.archetypes?.map((person) => <article key={person.id}><span>{person.id}</span><b>{person.label}</b><p>{person.summary}</p></article>) || <p>生成完成后，可在这里回看八个角色方向。</p>}</div>}
        {drawerTab === "selections" && <ol className="selection-list">{slots.map((slot) => <li key={slot.id}><button type="button" onClick={() => { setDrawerOpen(false); selectNode(slot); }}><span>{String(slot.index).padStart(2, "0")} · {slot.axis}</span><strong>{journey?.selections?.[slot.id]?.label || "未落契"}</strong></button></li>)}</ol>}
        {drawerTab === "card" && journey?.final_card && <section className="v2-preview">{(["description", "personality", "scenario", "first_mes", "alternate_greetings", "mes_example"] as const).map((field) => <article key={field}><h3>{field}</h3><p>{Array.isArray(cardData[field]) ? (cardData[field] as string[]).join("\n") : String(cardData[field] || "")}</p></article>)}</section>}
      </div>
      <footer>
        {completedCount === 12 && <button className="drawer-seal" type="button" onClick={() => { setDrawerOpen(false); setCompletionOpen(true); }}>{journey?.final_card ? "开始聊天" : "生成角色并开始聊天"}</button>}
        {journey?.final_card && <div className="v2-actions"><button type="button" onClick={() => void navigator.clipboard.writeText(JSON.stringify(journey.final_card, null, 2))}>复制 V2 JSON</button><button type="button" onClick={() => { const blob = new Blob([JSON.stringify(journey.final_card, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `${seed.ai_name || "character"}.json`; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 0); }}>下载 V2 JSON</button></div>}
      </footer>
    </aside></>}

    {completionOpen && <div className="completion-gate" data-no-pan role="dialog" aria-modal="true" aria-labelledby="completion-title">
      <section className="completion-panel">
        {!completionBusy && <button className="panel-close" type="button" onClick={() => setCompletionOpen(false)} aria-label="稍后生成">×</button>}
        <span className="completion-seal">成</span>
        <small>TWELVE TRAITS COMPLETE</small>
        <h2 id="completion-title">{journey?.status === "committed" ? "角色已入库" : "十二项已定"}</h2>
        <p>{journey?.status === "committed" ? <>角色已收入本地角色库。你可以直接继续与 <b>{seed.ai_name}</b> 聊天，或先前往角色库。</> : <>将这十二项选择写成完整角色卡，收入本地角色库，并直接进入与 <b>{seed.ai_name}</b> 的聊天。</>}</p>
        <div className="completion-steps">
          <span className="is-done"><i>✓</i><b>十二项选择已整理</b><small>12 / 12</small></span>
          <span className={`${journey?.final_card ? "is-done" : modelState === "synthesis" ? "is-active" : modelState === "synthesis_failed" ? "is-failed" : ""}`}><i>{journey?.final_card ? "✓" : "2"}</i><b>合成 V2 角色卡</b><small>{modelState === "synthesis" ? "正在调用模型" : journey?.final_card ? "已完成" : modelState === "synthesis_failed" ? "需要重试" : "等待"}</small></span>
          <span className={`${journey?.status === "committed" ? "is-done" : modelState === "commit" ? "is-active" : modelState === "commit_failed" ? "is-failed" : ""}`}><i>{journey?.status === "committed" ? "✓" : "3"}</i><b>收入本地角色库</b><small>{modelState === "commit" ? "正在写入" : journey?.status === "committed" ? "已完成" : modelState === "commit_failed" ? "需要重试" : "等待"}</small></span>
          <span className={`${modelState === "chat" ? "is-active" : modelState === "chat_failed" ? "is-failed" : ""}`}><i>4</i><b>进入本地聊天</b><small>{modelState === "chat" ? "正在创建会话" : modelState === "chat_failed" ? "需要重试" : "等待"}</small></span>
        </div>
        {error && completionOpen && <div className="completion-error" role="alert">{error}</div>}
        <button className="completion-primary" type="button" disabled={completionBusy} onClick={() => { if (modelState === "chat_failed") void retryChat(); else void finishCharacter(); }}>{completionButtonLabel}</button>
        {!completionBusy && <div className="completion-secondary">{journey?.status === "committed" ? <button type="button" onClick={() => { window.localStorage.removeItem(PENDING_CHAT_KEY); window.localStorage.removeItem(RESUME_KEY); exit?.(); }}>前往角色库</button> : <><button type="button" onClick={() => void stepBack()}>回退上一步</button><button type="button" onClick={() => { setCompletionOpen(false); setDrawerTab("selections"); setDrawerOpen(true); }}>回看十二项</button><button type="button" onClick={() => setCompletionOpen(false)}>稍后生成</button></>}</div>}
      </section>
    </div>}
  </main>;
}

export const DrawWorkshop = DestinyCanvas;
