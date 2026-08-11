import { useCallback, useState } from "react";

import { request } from "../../shared/api";
import type { ConversationScene } from "../../types";

export function useConversationScene() {
  const [conversationScene, setConversationScene] = useState<ConversationScene | null>(null);

  const loadConversationScene = useCallback(async (sessionId: string) => {
    try {
      const value = await request<ConversationScene>(
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/scene`,
      );
      setConversationScene(value);
      return value;
    } catch {
      setConversationScene(null);
      return null;
    }
  }, []);

  return { conversationScene, loadConversationScene, setConversationScene };
}
