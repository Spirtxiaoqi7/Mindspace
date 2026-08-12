import { useEffect, useRef } from "react";
import { closeOpenMenusOutside, composerAction } from "../chat-contract";
import type { ChatAttachment, InteractionTag, Message } from "../types";

type InteractionBranch = "root" | "touch" | "kiss";

function MicrophoneIcon() {
  return <svg className="composer-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.25a4 4 0 0 0 4-4V6a4 4 0 1 0-8 0v5.25a4 4 0 0 0 4 4Z" /><path d="M5.5 10.75v.5a6.5 6.5 0 0 0 13 0v-.5M12 17.75V21M9 21h6" /></svg>;
}

export interface ComposerProps {
  generating: boolean;
  characterName: string;
  input: string;
  onInput: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
  onOpenVoice: () => void;
  asrReady: boolean;
  hasPayload: boolean;
  replyTarget: Message | null;
  onClearReply: () => void;
  regenerationDraft: boolean;
  onCancelRegeneration: () => void;
  onSendRegeneration: () => void;
  pendingInteractions: InteractionTag[];
  onRemoveInteraction: (id: string) => void;
  pendingAttachments: ChatAttachment[];
  onRemoveAttachment: (attachment: ChatAttachment) => void;
  onAttachmentFiles: (files: FileList | null) => Promise<void>;
  interactionOpen: boolean;
  interactionBranch: InteractionBranch;
  onInteractionOpen: (open: boolean) => void;
  onInteractionBranch: (branch: InteractionBranch) => void;
  interactionTargets: { normal: string[]; intimate: string[] };
  onAddInteraction: (kind: "custom" | "daily" | "kiss" | "touch", action: string, target?: string, sensitivity?: "normal" | "intimate") => void;
  customInteraction: string;
  onCustomInteraction: (value: string) => void;
  round: number;
  onInitiative: () => void;
  sceneTitle: string;
  onOpenScenes: () => void;
  onShowFlow: () => void;
  onShowContext: () => void;
  retrievalCount: number;
  onExportSession: () => void;
  adultMode: boolean;
  onToggleAdultMode: () => void;
  r18StyleId: string;
  onR18StyleId: (value: string) => void;
  model: string;
  modelBaseUrl: string;
  modelToolLabel: string;
  modelsLoading: boolean;
  availableModels: string[];
  onLoadModels: () => void;
  onChooseModel: (model: string) => void;
  onClearCurrent: () => void;
}

export function Composer(props: ComposerProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const interactionCanvasRef = useRef<HTMLDivElement>(null);
  const actionKind = composerAction(props.generating, props.hasPayload, props.asrReady);
  const actionDisabled = actionKind === "voice-disabled";
  const gradedInteraction = props.interactionBranch === "touch" ? "touch" : "kiss";

  useEffect(() => {
    const handleOutsidePointer = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (props.interactionOpen && !interactionCanvasRef.current?.contains(target) && !target.closest(".interaction-entry, .message-more")) {
        props.onInteractionOpen(false);
        props.onInteractionBranch("root");
      }
      closeOpenMenusOutside(target);
    };
    document.addEventListener("pointerdown", handleOutsidePointer);
    return () => document.removeEventListener("pointerdown", handleOutsidePointer);
  }, [props.interactionOpen, props.onInteractionBranch, props.onInteractionOpen]);

  return <section className="composer-wrap">
    {props.generating && <div className="run-strip"><span><i /> 正在回应</span><button onClick={props.onCancel}>停止生成</button></div>}
    {props.interactionOpen && <div className="interaction-canvas" ref={interactionCanvasRef}><header><div><b>选择互动</b><span>可多选，选中后保留在输入框</span></div><button onClick={() => props.onInteractionOpen(false)}>×</button></header>{props.interactionBranch === "root" ? <div className="interaction-root"><section><small>日常交互</small><div>{["摸头", "牵手", "靠近"].map((action) => <button key={action} onClick={() => props.onAddInteraction("daily", action)}>{action}</button>)}</div></section><section><small>分级互动</small><div><button onClick={() => props.onInteractionBranch("touch")}><b>L1</b> 抚摸 <span>选择部位 ›</span></button><button onClick={() => props.onInteractionBranch("kiss")}><b>L2</b> 亲吻 <span>选择部位 ›</span></button></div></section><section className="custom-interaction"><small>自定义</small><div><input value={props.customInteraction} onChange={(event) => props.onCustomInteraction(event.target.value)} placeholder="输入一个互动" maxLength={40} /><button disabled={!props.customInteraction.trim()} onClick={() => { props.onAddInteraction("custom", props.customInteraction.trim()); props.onCustomInteraction(""); }}>加入</button></div></section></div> : <div className="interaction-targets"><button className="interaction-back" onClick={() => props.onInteractionBranch("root")}>‹ 返回</button><small>{props.interactionBranch === "touch" ? "L1 抚摸" : "L2 亲吻"} · 选择一个或多个部位</small><div>{props.interactionTargets.normal.map((target) => <button key={target} onClick={() => props.onAddInteraction(gradedInteraction, gradedInteraction === "touch" ? "抚摸" : "亲吻", target)}>{target}</button>)}{props.interactionTargets.intimate.map((target) => <button className="intimate" key={target} onClick={() => props.onAddInteraction(gradedInteraction, gradedInteraction === "touch" ? "抚摸" : "亲吻", target, "intimate")}>{target}<i>NSFW</i></button>)}</div></div>}</div>}
    <div className="composer">
      {(props.replyTarget || props.pendingInteractions.length > 0 || props.pendingAttachments.length > 0 || props.regenerationDraft) && <div className="composer-chips">{props.regenerationDraft && <button className="reply-chip" onClick={props.onCancelRegeneration}><span>重新生成</span>等待补齐原附件<b>×</b></button>}{props.replyTarget && <button className="reply-chip" disabled={props.regenerationDraft} onClick={props.onClearReply}><span>回复</span>{props.replyTarget.content.slice(0, 42) || "这条消息"}<b>×</b></button>}{props.pendingInteractions.map((item) => <button disabled={props.regenerationDraft} className={`interaction-chip ${item.sensitivity}`} key={item.id} onClick={() => props.onRemoveInteraction(item.id)}><span>{item.level ? `L${item.level}` : "日常"}</span>{item.action}{item.target ? ` · ${item.target}` : ""}<b>×</b></button>)}{props.pendingAttachments.map((item) => <button disabled={props.regenerationDraft} className="attachment-chip" key={item.attachment_id} onClick={() => props.onRemoveAttachment(item)}><span>{item.content_missing ? "待重附" : "附件"}</span>{item.name}<b>×</b></button>)}</div>}
      <textarea readOnly={props.regenerationDraft} value={props.input} onChange={(event) => props.onInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (props.regenerationDraft) props.onSendRegeneration(); else props.onSend(); } }} placeholder={`对 ${props.characterName} 说点什么…`} rows={1} />
      <div className="composer-row">
        <div className="composer-primary-tools">
          <input ref={fileInputRef} className="visually-hidden" type="file" multiple accept=".txt,.md,.json,.csv,text/*,application/json" onChange={(event) => { void props.onAttachmentFiles(event.target.files); event.currentTarget.value = ""; }} />
          <button className={`interaction-entry${props.interactionOpen ? " active" : ""}`} onClick={() => { props.onInteractionOpen(!props.interactionOpen); props.onInteractionBranch("root"); }}>互动{props.pendingInteractions.length ? ` ${props.pendingInteractions.length}` : ""}</button>
          <button className="initiative-inline" disabled={props.generating} onClick={props.onInitiative}>✦ 让 {props.characterName} 先说</button>
          <details className="composer-menu composer-add-menu"><summary aria-label="更多对话功能"><span className="visually-hidden">更多</span><b aria-hidden="true">＋</b></summary><div><button onClick={() => fileInputRef.current?.click()}>上传文件</button><button onClick={props.onOpenScenes}>场景 · {props.sceneTitle || "未选择"}</button><button onClick={props.onShowFlow}>会话流程与执行详情</button><button onClick={props.onShowContext}>RAG 引用 <b>{props.retrievalCount}</b></button><button onClick={props.onExportSession}>导出当前会话</button><button className={`adult-entry${props.adultMode ? " active" : ""}`} aria-label="NSFW" aria-pressed={props.adultMode} onClick={props.onToggleAdultMode}>NSFW <span>{props.adultMode ? "已开启" : "已关闭"}</span></button>{props.adultMode && <label className="r18-style-menu-label"><span>成人模式风格</span><select className="r18-style-select" value={props.r18StyleId} aria-label="R18 风格包" onChange={(event) => props.onR18StyleId(event.target.value)}><option value="high_intensity">高强度推进</option><option value="immersive_narrative">叙事沉浸</option><option value="dialogue_led">台词主导</option></select></label>}<button className="composer-clear-action" onClick={props.onClearCurrent}>清空当前上下文</button></div></details>
        </div>
        <details className="model-quick-menu" onToggle={(event) => { if (event.currentTarget.open) props.onLoadModels(); }}><summary title={props.modelBaseUrl}>{props.model || "选择模型"}</summary><div><small>当前 API URL 可用模型</small><small>{props.modelToolLabel}</small>{props.modelsLoading && <span>正在读取…</span>}{props.availableModels.map((model) => <button className={model === props.model ? "active" : ""} key={model} onClick={(event) => { event.currentTarget.closest("details")?.removeAttribute("open"); props.onChooseModel(model); }}>{model}</button>)}</div></details>
        <button className={`send${actionKind === "voice" ? " voice" : ""}${actionDisabled ? " voice-disabled" : ""}`} disabled={actionDisabled} onClick={actionKind === "cancel" ? props.onCancel : actionKind === "send" ? (props.regenerationDraft ? props.onSendRegeneration : props.onSend) : props.onOpenVoice} aria-label={actionKind === "cancel" ? "停止生成" : actionKind === "send" ? "发送消息" : actionKind === "voice" ? "开始语音" : "语音未启用，请先配置"} title={actionDisabled ? "语音未启用，请先在设置中配置 ASR" : undefined}>{actionKind === "cancel" ? "■" : actionKind === "send" ? "↑" : <MicrophoneIcon />}</button>
      </div>
    </div>
    <div className="composer-meta"><span>Enter 发送 · Shift+Enter 换行 · Esc 打断</span><span>{props.adultMode ? "NSFW 已开启" : "表达方式自动适配"}</span></div>
  </section>;
}
