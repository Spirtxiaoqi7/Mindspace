import type { CSSProperties } from "react";
import type { AvatarConfig, AvatarEntry, Role } from "../types";

export const DEFAULT_AVATARS: AvatarConfig = {
  user: { src: "/assets/avatar-user-default.webp", aspect: "2 / 3", scale: 1.08, x: -12, y: 0 },
  assistant: { src: "/assets/avatar-ai-default.webp", aspect: "2 / 3", scale: 1, x: 0, y: 0 },
};

const ASPECTS: AvatarEntry["aspect"][] = ["2 / 3", "3 / 4", "4 / 5", "9 / 16", "1 / 1"];
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const number = (value: unknown, fallback: number) => Number.isFinite(Number(value)) ? Number(value) : fallback;

export function normalizeAvatarConfig(value: unknown): AvatarConfig {
  const raw = record(value);
  const normalized = {} as AvatarConfig;
  (["user", "assistant"] as Role[]).forEach((role) => {
    const fallback = DEFAULT_AVATARS[role];
    const entry = record(raw[role]);
    const aspect = String(entry.aspect || fallback.aspect) as AvatarEntry["aspect"];
    normalized[role] = {
      src: String(entry.src || fallback.src),
      aspect: ASPECTS.includes(aspect) ? aspect : fallback.aspect,
      scale: Math.max(0.6, Math.min(3, number(entry.scale, fallback.scale))),
      x: Math.max(-80, Math.min(80, number(entry.x, fallback.x))),
      y: Math.max(-80, Math.min(80, number(entry.y, fallback.y))),
    };
  });
  return normalized;
}

export function avatarStyle(entry: AvatarEntry): CSSProperties {
  return {
    "--avatar-aspect": entry.aspect,
    "--avatar-scale": entry.scale,
    "--avatar-x": `${entry.x}%`,
    "--avatar-y": `${entry.y}%`,
  } as CSSProperties;
}

export function PortraitAvatar({ role, avatars, label, onClick, className = "" }: {
  role: Role;
  avatars: AvatarConfig;
  label: string;
  onClick?: () => void;
  className?: string;
}) {
  const entry = avatars[role];
  const fallback = DEFAULT_AVATARS[role].src;
  return <button type="button" className={`portrait-avatar ${className}`} style={avatarStyle(entry)} onClick={onClick} title={`查看${label}人物卡`} aria-label={`查看${label}人物卡`}><img src={entry.src} alt={`${label}头像`} onError={(event) => { if (!event.currentTarget.src.endsWith(fallback)) event.currentTarget.src = fallback; }} /></button>;
}
