import type { CSSProperties } from "react";

import type {
  AvatarEntry,
  VoiceInteractionContext,
  VoicePhase,
  VoiceSessionState,
} from "../../types";
import { avatarStyle } from "../../ui/avatar";

const VOICE_LABELS: Record<VoicePhase, string> = {
  idle: "准备开始",
  preparing: "正在准备麦克风",
  connecting: "正在连接语音服务",
  listening: "我在听，请说话",
  "user-speaking": "正在聆听",
  collecting: "已收到，等待你继续说",
  deferred: "已听到，等回应结束后发送",
  transcribing: "正在确认你说的话",
  thinking: "正在思考并流式回复",
  "assistant-speaking": "正在回应你",
  "candidate-interruption": "听到声音，正在确认",
  interrupted: "已打断，继续说吧",
  error: "语音服务暂时不可用",
};

interface VoiceModeProps {
  state: VoiceSessionState;
  avatar: AvatarEntry;
  characterName: string;
  context: VoiceInteractionContext;
  companion: {
    enabled: boolean;
    round: number;
    limit: number;
  };
  onExit: () => void;
  onRetry: () => void;
  onFallback: () => void;
}

export function VoiceMode({
  state,
  avatar,
  characterName,
  context,
  companion,
  onExit,
  onRetry,
  onFallback,
}: VoiceModeProps) {
  const intensity = Math.max(0.08, state.level);
  const faceToFace = context.mode === "face_to_face";

  return (
    <section
      className={`voice-mode phase-${state.phase}`}
      style={{
        "--voice-level": intensity,
        "--voice-avatar": `url("${avatar.src}")`,
      } as CSSProperties}
      aria-label="沉浸式实时语音"
    >
      <div className="voice-background" />
      <div className="voice-shade" />
      <button className="voice-exit" onClick={onExit}>退出语音</button>
      <div className="voice-stage">
        <span className="voice-kicker">{faceToFace ? "FACE TO FACE" : "LIVE CONVERSATION"}</span>
        {faceToFace && (
          <div className="voice-scene-chip" title={context.scene || "普通面对面场景"}>
            <span>面对面</span>
            <small>{context.scene || "未指定具体场景"}</small>
          </div>
        )}
        {companion.enabled && (
          <div className={`voice-companion ${companion.round >= companion.limit ? "complete" : ""}`} role="status">
            <span>连续陪伴</span>
            <strong>{companion.round} / {companion.limit}</strong>
            <small>{companion.round >= companion.limit ? "已到本次上限" : "朗读结束 10 秒后继续 · 可随时插话"}</small>
          </div>
        )}
        <div className="voice-portrait-shell">
          <i className="voice-ring ring-one" />
          <i className="voice-ring ring-two" />
          <div className="voice-portrait portrait-avatar" style={avatarStyle(avatar)}>
            <img src={avatar.src} alt={`${characterName}头像`} />
          </div>
        </div>
        <h1>{characterName}</h1>
        <div className="voice-status"><i />{VOICE_LABELS[state.phase]}</div>
        <div className="voice-wave" aria-hidden="true">
          {Array.from({ length: 18 }, (_, index) => (
            <i key={index} style={{ "--bar": (index % 5) + 1 } as CSSProperties} />
          ))}
        </div>
        <div className="voice-caption">
          <small>{state.reply ? `${characterName} 正在回应` : "你刚刚说"}</small>
          <p>{state.reply || state.transcript || ((state.phase === "error" || state.phase === "preparing") ? state.error : "直接开始说话，我会自动识别、发送并回应。")}</p>
        </div>
        {(state.phase === "error" || state.phase === "preparing") && (
          <div className="voice-error">
            <span>{state.error}</span>
            <button onClick={onRetry}>重试原生采集</button>
            <button onClick={onFallback}>切换备用采集</button>
          </div>
        )}
        <span className="voice-tip">连续说话确认后才会打断 · 插话会重定向话题 · Ctrl+Shift+M 切换 · Esc 退出</span>
      </div>
    </section>
  );
}
