export const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? (value as Record<string, unknown>) : {};

export const bool = (value: unknown) => Boolean(value);

export const num = (value: unknown, fallback = 0) =>
  Number.isFinite(Number(value)) ? Number(value) : fallback;

export const str = (value: unknown) => String(value ?? "");

export function friendlyValue(value: unknown): string {
  if (value == null || value === "") return "暂无";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(friendlyValue).join("、") || "暂无";
  return Object.entries(asRecord(value)).map(([key, item]) => key + "：" + friendlyValue(item)).join("；") || "暂无";
}

export function formatTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? ""
    : new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
}