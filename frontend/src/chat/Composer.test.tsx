import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { Composer, type ComposerProps } from "./Composer";

function props(overrides: Partial<ComposerProps> = {}): ComposerProps {
  return {
    generating: false,
    characterName: "角色",
    input: "",
    onInput: vi.fn(),
    onSend: vi.fn(),
    onCancel: vi.fn(),
    onOpenVoice: vi.fn(),
    asrReady: false,
    hasPayload: false,
    replyTarget: null,
    onClearReply: vi.fn(),
    regenerationDraft: false,
    onCancelRegeneration: vi.fn(),
    onSendRegeneration: vi.fn(),
    pendingInteractions: [],
    onRemoveInteraction: vi.fn(),
    pendingAttachments: [],
    onRemoveAttachment: vi.fn(),
    onAttachmentFiles: vi.fn().mockResolvedValue(undefined),
    interactionOpen: false,
    interactionBranch: "root",
    onInteractionOpen: vi.fn(),
    onInteractionBranch: vi.fn(),
    interactionTargets: { normal: [], intimate: [] },
    onAddInteraction: vi.fn(),
    customInteraction: "",
    onCustomInteraction: vi.fn(),
    round: 1,
    onInitiative: vi.fn(),
    sceneTitle: "",
    onOpenScenes: vi.fn(),
    onShowFlow: vi.fn(),
    onShowContext: vi.fn(),
    retrievalCount: 0,
    onExportSession: vi.fn(),
    adultMode: false,
    onToggleAdultMode: vi.fn(),
    r18StyleId: "high_intensity",
    onR18StyleId: vi.fn(),
    model: "model-a",
    modelBaseUrl: "https://example.test/v1",
    modelToolLabel: "工具能力由 Provider 决定",
    modelsLoading: false,
    availableModels: [],
    onLoadModels: vi.fn(),
    onChooseModel: vi.fn(),
    onClearCurrent: vi.fn(),
    ...overrides,
  };
}

it("shows voice only when live ASR is ready and switches to send for payload", async () => {
  const user = userEvent.setup();
  const openVoice = vi.fn();
  const { rerender } = render(<Composer {...props({ asrReady: true, onOpenVoice: openVoice })} />);
  await user.click(screen.getByRole("button", { name: "开始语音" }));
  expect(openVoice).toHaveBeenCalledOnce();

  const send = vi.fn();
  rerender(<Composer {...props({ asrReady: true, hasPayload: true, input: "你好", onSend: send })} />);
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  expect(send).toHaveBeenCalledOnce();
});

it("keeps missing attachment metadata visible and removable before regeneration", async () => {
  const user = userEvent.setup();
  const attachment = { attachment_id: "a", name: "notes.txt", media_type: "text/plain", size: 10, content_missing: true };
  const remove = vi.fn();
  render(<Composer {...props({ regenerationDraft: true, pendingAttachments: [attachment], onRemoveAttachment: remove })} />);
  expect(screen.getByText("待重附")).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toHaveAttribute("readonly");
  expect(screen.getByRole("button", { name: /notes\.txt/ })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: /等待补齐原附件/ }));
  expect(remove).not.toHaveBeenCalled();
});
