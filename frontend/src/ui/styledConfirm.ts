export interface ConfirmationOptions {
  title: string;
  message: string;
  detail?: string;
  confirmLabel?: string;
  danger?: boolean;
}

export function styledConfirm(options: ConfirmationOptions): Promise<boolean> {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "confirmation-backdrop";
    const card = document.createElement("section");
    card.className = `confirmation-card${options.danger ? " danger" : ""}`;
    card.setAttribute("role", "alertdialog");
    card.setAttribute("aria-modal", "true");
    const mark = document.createElement("span");
    mark.className = "confirmation-mark";
    mark.textContent = options.danger ? "!" : "◇";
    const copy = document.createElement("div");
    copy.className = "confirmation-copy";
    const kicker = document.createElement("small");
    kicker.textContent = options.danger ? "需要确认" : "确认操作";
    const title = document.createElement("h2");
    title.textContent = options.title;
    const message = document.createElement("p");
    message.textContent = options.message;
    copy.append(kicker, title, message);
    if (options.detail) {
      const detail = document.createElement("span");
      detail.textContent = options.detail;
      copy.append(detail);
    }
    const actions = document.createElement("footer");
    const cancel = document.createElement("button");
    cancel.className = "confirmation-cancel";
    cancel.textContent = "取消";
    const confirm = document.createElement("button");
    confirm.className = "confirmation-accept";
    confirm.textContent = options.confirmLabel || "继续";
    actions.append(cancel, confirm);
    card.append(mark, copy, actions);
    backdrop.append(card);
    let settled = false;
    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      document.removeEventListener("keydown", onKeyDown);
      backdrop.remove();
      resolve(value);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") finish(false);
    };
    cancel.addEventListener("click", () => finish(false));
    confirm.addEventListener("click", () => finish(true));
    backdrop.addEventListener("mousedown", (event) => {
      if (event.target === backdrop) finish(false);
    });
    document.addEventListener("keydown", onKeyDown);
    document.body.append(backdrop);
    window.requestAnimationFrame(() => {
      backdrop.classList.add("visible");
      confirm.focus();
    });
  });
}
