import { memo } from "react";
import { shouldRenderToolExecution } from "../chat-contract";
import type { AvatarConfig, Message, Role } from "../types";
import { PortraitAvatar } from "../ui/avatar";
import { ToolCard } from "./ExecutionInspector";

function richText(text: string) {
  const parts = text.split(/(```[\s\S]*?```|`[^`]+`)/g);
  return parts.map((part, index) => {
    if (part.startsWith("```") && part.endsWith("```")) return <pre key={index}><code>{part.slice(3, -3).trim()}</code></pre>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    const lines = part.split("\n");
    return lines.map((line, lineIndex) => <span key={`${index}-${lineIndex}`}>{line}{lineIndex < lines.length - 1 && <br />}</span>);
  });
}

export interface MessageListProps {
  messages: Message[];
  avatars: AvatarConfig;
  userName: string;
  characterName: string;
  onProfile: (role: Role) => void;
  onCopy: (text: string) => void;
  onSpeak: (text: string) => void;
  onRegenerate: (message: Message, round: number) => void;
  onInitiative: (round: number) => void;
  onDelete: (messageId: string) => void;
  onConfigure: () => void;
  onReply: (message: Message) => void;
  onInteract: (message: Message) => void;
}

export const MessageList = memo(function MessageList({ messages, avatars, userName, characterName, onProfile, onCopy, onSpeak, onRegenerate, onInitiative, onDelete, onConfigure, onReply, onInteract }: MessageListProps) {
  return <div className="message-list">{messages.map((message, index) => {
    const label = message.role === "user" ? userName : characterName;
    const retry = message.status === "error";
    return <article className={`message ${message.role} ${message.status || "complete"}`} key={`${message.message_id || message.round}-${message.role}-${index}`}>
      <div className="message-avatar-column"><PortraitAvatar role={message.role} avatars={avatars} label={label} onClick={() => onProfile(message.role)} /><span>第 {message.round} 轮</span></div>
      <div className="message-card"><header><strong>{label}</strong><details className="message-more" onToggle={(event) => { if (event.currentTarget.open) onReply(message); }}><summary>更多</summary><div><button onClick={() => onInteract(message)}>互动</button></div></details></header>
        {message.reply_to_message_id && <div className="message-reference">@本条消息</div>}
        {message.interactions?.length ? <div className="message-interactions">{message.interactions.map((item) => <span key={item.id}>{item.action}{item.target ? ` · ${item.target}` : ""}</span>)}</div> : null}
        {message.attachments?.length ? <div className="message-attachments">{message.attachments.map((item) => <span key={item.attachment_id}>{item.name}</span>)}</div> : null}
        {shouldRenderToolExecution(message.tool_execution) && <ToolCard tool={message.tool_execution} />}
        <div className="message-content">{richText(message.content)}</div>
        <footer><button onClick={() => onReply(message)}>@本条消息</button><button onClick={() => onCopy(message.content)}>复制</button>{message.role === "assistant" && <button onClick={() => onSpeak(message.content)}>朗读</button>}{message.role === "assistant" && <button onClick={() => onRegenerate(message, message.round)}>重新生成</button>}{message.role === "assistant" && <button onClick={() => onDelete(message.message_id || "")}>删除回复</button>}{retry && <button onClick={onConfigure}>立即配置 API</button>}{retry && <button onClick={() => onInitiative(message.round)}>重新尝试</button>}</footer>
      </div>
    </article>;
  })}</div>;
});
