import type { ComponentProps, ReactNode, RefObject } from "react";

import { Composer } from "../../chat/Composer";
import { MessageList } from "../../chat/MessageList";
import { formatTime } from "../../shared/formatters";
import type { AvatarConfig, SessionSummary } from "../../types";
import { avatarStyle, DEFAULT_AVATARS, PortraitAvatar } from "../../ui/avatar";

export interface ChatNavigationViewModel {
  sidebarOpen: boolean;
  search: string;
  sessions: SessionSummary[];
  sessionId: string;
  avatars: AvatarConfig;
  characterName: string;
  onHome: () => void;
  onCloseSidebar: () => void;
  onSearch: (value: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onOpenMemory: () => void;
  onOpenProfileCard: () => void;
  onOpenProfileEditor: () => void;
}

export interface ChatConversationViewModel {
  sceneActive: boolean;
  sceneKey: string;
  sceneBackgroundImage: string;
  title: string;
  characterName: string;
  userName: string;
  generating: boolean;
  round: number;
  settingsReady: boolean;
  settingsTitle: string;
  avatars: AvatarConfig;
  conversationRef: RefObject<HTMLDivElement | null>;
  conversationTailRef: RefObject<HTMLDivElement | null>;
  onConversationScroll: () => void;
  onPauseConversationFollow: () => void;
  onOpenSidebar: () => void;
  onOpenProfileEditor: () => void;
  onOpenSettings: () => void;
  welcome: {
    visible: boolean;
    relationshipLabel: string;
    userAlias: string;
    onOpenProfileCard: () => void;
    onInitiative: () => void;
    onOpenScenes: () => void;
    onOpenProfileEditor: () => void;
  };
  messageList: ComponentProps<typeof MessageList>;
}

export type ChatComposerViewModel = ComponentProps<typeof Composer>;

export interface ChatOverlayViewModel {
  content: ReactNode;
}

interface ChatWorkspaceProps {
  navigation: ChatNavigationViewModel;
  conversation: ChatConversationViewModel;
  composer: ChatComposerViewModel;
  overlays: ChatOverlayViewModel;
  inspectorOpen: boolean;
}

function ChatSidebar({ navigation }: { navigation: ChatNavigationViewModel }) {
  return (
    <aside className={`sidebar ${navigation.sidebarOpen ? "mobile-open" : ""}`}>
      <div className="brand-row">
        <button className="brand-mark" onClick={navigation.onHome} title="返回主页" aria-label="Mindspace 主页">
          <img src={`${import.meta.env.BASE_URL}assets/mindspace-brand-icon.png?v=0.8.1`} alt="" />
        </button>
        <div><strong>Mindspace</strong><small>PRIVATE COMPANION</small></div>
        <button className="icon-button mobile-only" onClick={navigation.onCloseSidebar} aria-label="关闭会话栏">×</button>
      </div>
      <button className="new-chat home-entry" onClick={navigation.onHome}><span>⌂</span> 主页</button>
      <label className="search-box">
        <span>⌕</span>
        <input value={navigation.search} onChange={(event) => navigation.onSearch(event.target.value)} placeholder="搜索会话" aria-label="搜索会话" />
      </label>
      <div className="session-heading"><span>最近会话</span><small>{navigation.sessions.length}</small></div>
      <nav className="session-list">
        {navigation.sessions.length ? navigation.sessions.map((item) => (
          <div className={`session-item ${item.session_id === navigation.sessionId ? "active" : ""}`} key={item.session_id}>
            <button className="session-open" onClick={() => navigation.onOpenSession(item.session_id)}>
              {item.character_avatar?.src ? (
                <img className="session-avatar" src={item.character_avatar.src} alt="" onError={(event) => { event.currentTarget.src = DEFAULT_AVATARS.assistant.src; }} />
              ) : <span className="session-glyph">◌</span>}
              <span><strong>{item.character_name || item.title}</strong><small>{item.message_count} 条 · {formatTime(item.updated_at)}</small></span>
            </button>
            <button className="session-delete" aria-label={`删除会话：${item.character_name || item.title}`} title="删除会话" onClick={() => navigation.onDeleteSession(item.session_id)}>×</button>
          </div>
        )) : <div className="empty-mini">没有匹配的会话</div>}
      </nav>
      <div className="sidebar-tools hub-navigation">
        <button className="sidebar-memory-entry" onClick={navigation.onOpenMemory}><span>◇</span><b>记忆</b><i>事件与长期记忆</i></button>
      </div>
      <div className="account-card">
        <PortraitAvatar role="assistant" avatars={navigation.avatars} label={navigation.characterName} className="small" onClick={navigation.onOpenProfileCard} />
        <button className="account-settings persona-entry" aria-label="打开人设工作区" onClick={navigation.onOpenProfileEditor}>
          <span><strong>{navigation.characterName}</strong><small><i /> 人物、状态与关系</small></span><b>人设</b>
        </button>
      </div>
    </aside>
  );
}

function ChatConversation({ conversation, composer }: { conversation: ChatConversationViewModel; composer: ChatComposerViewModel }) {
  return (
    <main className={`workspace${conversation.sceneActive ? " scene-active" : ""}`}>
      {conversation.sceneActive && (
        <div key={conversation.sceneKey} className="chat-scene-background" style={{ backgroundImage: conversation.sceneBackgroundImage }} aria-hidden="true" />
      )}
      <header className="topbar">
        <button className="mobile-only mobile-menu" onClick={conversation.onOpenSidebar} aria-label="打开会话栏">☰</button>
        <div className="title-block">
          <span className="topbar-kicker">CONVERSATION</span>
          <h1>{conversation.title}</h1>
          <span>{conversation.characterName} · {conversation.generating ? "正在回应" : `第 ${conversation.round} 轮 · 已就绪`}</span>
        </div>
        <div className="top-actions">
          <button className="top-character-entry" onClick={conversation.onOpenProfileEditor} title="打开人设工作区">
            <span className="top-character-avatar" style={avatarStyle(conversation.avatars.assistant)} aria-hidden="true"><img src={conversation.avatars.assistant.src} alt="" /></span>
            <span>{conversation.characterName}</span>
          </button>
          <button className="top-settings-entry" onClick={conversation.onOpenSettings} title={conversation.settingsTitle}>
            <i className={conversation.settingsReady ? "ready" : "warning"} />⚙ <span>设置</span>
          </button>
        </div>
      </header>
      <section
        className="conversation"
        ref={conversation.conversationRef}
        onScroll={conversation.onConversationScroll}
        onWheel={(event) => { if (event.deltaY < 0) conversation.onPauseConversationFollow(); }}
        onTouchMove={conversation.onPauseConversationFollow}
      >
        {conversation.welcome.visible && (
          <div className="welcome-panel companion-stage">
            <div className="stage-portrait"><PortraitAvatar role="assistant" avatars={conversation.avatars} label={conversation.characterName} onClick={conversation.welcome.onOpenProfileCard} /></div>
            <span className="eyebrow">{conversation.welcome.relationshipLabel}</span>
            <h2>{conversation.userName} <i>×</i> {conversation.characterName}</h2>
            <blockquote>“{conversation.welcome.userAlias}，我在。今天想从哪里开始？”</blockquote>
            <div className="stage-actions">
              <button className="stage-speak" disabled={conversation.generating} onClick={conversation.welcome.onInitiative}><span>✦</span><b>让 {conversation.characterName} 先说</b><small>由当前人设与场景发起一句话</small></button>
              <button onClick={conversation.welcome.onOpenScenes}><span>⌑</span><b>{composer.sceneTitle || "选择场景"}</b><small>改变这次见面的环境</small></button>
              <button onClick={conversation.welcome.onOpenProfileEditor}><span>◇</span><b>查看人设</b><small>人物、关系与运行状态</small></button>
            </div>
          </div>
        )}
        <MessageList {...conversation.messageList} />
        <div className="conversation-tail" ref={conversation.conversationTailRef} aria-hidden="true" />
      </section>
      <Composer {...composer} />
    </main>
  );
}

export function ChatWorkspace({ navigation, conversation, composer, overlays, inspectorOpen }: ChatWorkspaceProps) {
  return (
    <div className={`app-shell ${inspectorOpen ? "inspector-visible" : "inspector-hidden"}`}>
      <ChatSidebar navigation={navigation} />
      <ChatConversation conversation={conversation} composer={composer} />
      {overlays.content}
    </div>
  );
}
