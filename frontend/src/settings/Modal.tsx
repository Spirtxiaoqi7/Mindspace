import type { ReactNode } from "react";

export function Modal({ title, kicker, onClose, children, footer, compact = false, className = "", dismissOnBackdrop = false }: { title: string; kicker: string; onClose: () => void; children: ReactNode; footer?: ReactNode; compact?: boolean; className?: string; dismissOnBackdrop?: boolean }) {
  const displayTitle = title === "记忆中心" ? "记忆" : title;
  const displayKicker = kicker === "MEMORY CENTER" ? "MEMORY" : kicker;
  return <div className="modal-backdrop" onMouseDown={(event) => {
    if (event.target !== event.currentTarget) return;
    if (dismissOnBackdrop) onClose();
    else event.preventDefault();
  }}><section className={`modal-card ${compact ? "compact" : ""} ${className}`.trim()} role="dialog" aria-modal="true" aria-label={displayTitle}><header><div><span className="eyebrow">{displayKicker}</span><h2>{displayTitle}</h2></div><button onClick={onClose} aria-label={`关闭${displayTitle}`}>×</button></header><div className="modal-body">{children}</div>{footer && <footer>{footer}</footer>}</section></div>;
}
