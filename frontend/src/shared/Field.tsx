import { bool, str } from "./formatters";

export function Field({ label, value, type = "text", onChange, min, max, step, placeholder }: { label: string; value: unknown; type?: string; onChange: (value: unknown) => void; min?: number; max?: number; step?: number; placeholder?: string }) {
  if (type === "checkbox") return <label className="toggle-field"><span>{label}</span><input type="checkbox" checked={bool(value)} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
  if (type === "textarea") return <label className="field wide"><span>{label}</span><textarea value={str(value)} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} rows={4} /></label>;
  return <label className="field"><span>{label}</span><input type={type} value={str(value)} placeholder={placeholder} min={min} max={max} step={step} onChange={(event) => onChange(type === "number" ? Number(event.target.value) : event.target.value)} /></label>;
}
