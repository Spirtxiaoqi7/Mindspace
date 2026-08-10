import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { MessageList } from "./chat/MessageList";
import type { AvatarConfig, Message } from "./types";

const avatars: AvatarConfig = {
  user: { src: "/user.webp", aspect: "1 / 1", scale: 1, x: 0, y: 0 },
  assistant: { src: "/assistant.webp", aspect: "1 / 1", scale: 1, x: 0, y: 0 },
};

it("keeps the product behavior where opening More immediately references the message", async () => {
  const user = userEvent.setup();
  const onReply = vi.fn();
  const onInteract = vi.fn();
  const messages: Message[] = [
    { message_id: "user-1", role: "user", content: "你好", round: 1, status: "complete" },
    { message_id: "assistant-1", role: "assistant", content: "我在", round: 1, status: "complete", tool_execution: null },
  ];
  render(<MessageList messages={messages} avatars={avatars} userName="用户" characterName="角色" onProfile={vi.fn()} onCopy={vi.fn()} onSpeak={vi.fn()} onRegenerate={vi.fn()} onInitiative={vi.fn()} onDelete={vi.fn()} onConfigure={vi.fn()} onReply={onReply} onInteract={onInteract} />);

  const summaries = screen.getAllByText("更多");
  await user.click(summaries[1]);
  expect(onReply).toHaveBeenCalledWith(messages[1]);
  const menu = summaries[1].closest("details")!;
  expect(menu).toHaveAttribute("open");

  await user.click(screen.getAllByRole("button", { name: "互动" })[1]);
  expect(onInteract).toHaveBeenCalledWith(messages[1]);
  expect(menu).toHaveAttribute("open");
  expect(screen.queryByText("任务处理")).not.toBeInTheDocument();
});
