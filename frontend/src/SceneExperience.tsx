import { useEffect, useMemo, useRef, useState } from "react";
import { request } from "./api";
import type {
  CharacterSummary,
  ConversationScene,
  SceneDefinition,
} from "./types";

export const sceneAssetPath = (scene: SceneDefinition | string) =>
  typeof scene === "string"
    ? `/assets/archive/scenes/${scene}.webp`
    : scene.asset_url || `/assets/archive/scenes/${scene.asset_id}.webp`;

function ArrowLeftIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M14.5 5.5 8 12l6.5 6.5M8.5 12H20" />
  </svg>;
}

function SceneIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M4 17.5 9.2 12l3.1 3.1 2.3-2.3L20 18M6.5 8.2h.01" />
    <rect x="3.5" y="4" width="17" height="16" rx="3" />
  </svg>;
}

export function ScenePickerPage({
  character,
  sessionId,
  current,
  onBack,
  onChanged,
  notify,
}: {
  character: CharacterSummary;
  sessionId: string;
  current: ConversationScene | null;
  onBack: () => void;
  onChanged: (scene: ConversationScene) => void;
  notify: (message: string) => void;
}) {
  const [scenes, setScenes] = useState<SceneDefinition[]>([]);
  const [previewId, setPreviewId] = useState(current?.scene?.scene_id || "");
  const [busyId, setBusyId] = useState("");
  const uploadInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let disposed = false;
    request<{ items: SceneDefinition[] }>("/api/v1/scenes")
      .then((result) => {
        if (disposed) return;
        setScenes(result.items);
        setPreviewId((value) =>
          value || current?.scene?.scene_id || result.items[0]?.scene_id || ""
        );
      })
      .catch((error: Error) => notify(error.message));
    return () => { disposed = true; };
  }, [current?.scene?.scene_id, notify]);

  const preview = useMemo(
    () => scenes.find((scene) => scene.scene_id === previewId)
      || current?.scene
      || scenes[0]
      || null,
    [current?.scene, previewId, scenes],
  );

  const select = async (scene: SceneDefinition) => {
    if (busyId || current?.scene?.scene_id === scene.scene_id) {
      setPreviewId(scene.scene_id);
      return;
    }
    setPreviewId(scene.scene_id);
    setBusyId(scene.scene_id);
    try {
      const updated = await request<ConversationScene>(
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/scene`,
        {
          method: "PUT",
          body: JSON.stringify({
            scene_id: scene.scene_id,
            expected_revision: current?.revision || 0,
          }),
        },
      );
      onChanged(updated);
      notify(`当前对话已切换到「${scene.title}」`);
    } finally {
      setBusyId("");
    }
  };

  const uploadCustomScene = async (file?: File) => {
    if (!file || busyId) return;
    if (file.size > 12 * 1024 * 1024) {
      notify("背景图片不能超过 12 MiB");
      return;
    }
    const form = new FormData();
    form.append("file", file);
    form.append("title", file.name.replace(/\.[^.]+$/, "").slice(0, 80) || "自定义场景");
    setBusyId("custom-upload");
    try {
      const scene = await request<SceneDefinition>("/api/v1/scenes/custom", { method: "POST", body: form });
      setScenes((items) => [...items.filter((item) => item.scene_id !== scene.scene_id), scene]);
      setPreviewId(scene.scene_id);
      const updated = await request<ConversationScene>(
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/scene`,
        {
          method: "PUT",
          body: JSON.stringify({ scene_id: scene.scene_id, expected_revision: current?.revision || 0 }),
        },
      );
      onChanged(updated);
      notify(`已上传并切换到「${scene.title}」`);
    } finally {
      setBusyId("");
      if (uploadInput.current) uploadInput.current.value = "";
    }
  };

  return <main
    className="scene-experience"
    style={preview
      ? { backgroundImage: `url("${sceneAssetPath(preview)}")` }
      : undefined}
  >
    <div className="scene-experience-shade" />
    <header className="scene-experience-header">
      <button className="chapter-nav-button" onClick={onBack}>
        <ArrowLeftIcon />
        <span>返回对话</span>
      </button>
      <div className="scene-experience-title">
        <span><SceneIcon /> 当前对话场景</span>
        <h1>{preview?.title || "选择一个地方"}</h1>
        <p>{preview?.description || `为你和${character.display_name}选择一处共同环境。`}</p>
      </div>
      <div className="scene-character-chip">
        <strong>{character.display_name}</strong>
        <span>{character.relationship_label}</span>
      </div>
    </header>

    <section className="scene-selection-panel" aria-label="切换当前对话背景">
      <div className="scene-selection-copy">
        <span>点击即切换</span>
        <p>背景与下一轮场景描述会同时更新，不创建活动、任务或共同片段。</p>
      </div>
      <div className="scene-selection-rail">
        <label className={`scene-upload-choice${busyId === "custom-upload" ? " is-uploading" : ""}`}>
          <input
            ref={uploadInput}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            aria-label="上传自定义场景背景"
            disabled={Boolean(busyId)}
            onChange={(event) => void uploadCustomScene(event.target.files?.[0])}
          />
          <b>＋</b>
          <span><strong>自定义背景</strong><small>{busyId === "custom-upload" ? "正在保存" : "PNG / JPEG / WebP"}</small></span>
        </label>
        {scenes.map((scene) => {
          const selected = current?.scene?.scene_id === scene.scene_id;
          return <button
            key={scene.scene_id}
            className={`scene-choice${selected ? " selected" : ""}`}
            aria-pressed={selected}
            aria-label={`切换到${scene.title}`}
            disabled={Boolean(busyId)}
            onMouseEnter={() => setPreviewId(scene.scene_id)}
            onFocus={() => setPreviewId(scene.scene_id)}
            onClick={() => void select(scene)}
          >
            <img src={sceneAssetPath(scene)} alt="" loading="lazy" />
            <span>
              <strong>{scene.title}</strong>
              <small>{selected ? "正在使用" : busyId === scene.scene_id ? "切换中" : scene.location}</small>
            </span>
          </button>;
        })}
      </div>
    </section>
  </main>;
}
