import { useEffect, useState } from "react";

import { request } from "../../shared/api";
import { friendlyValue } from "../../shared/formatters";
import { Modal } from "../../shared/Modal";
import type { AvatarConfig, ProfileCardData, Role } from "../../types";
import { PortraitAvatar } from "../../ui/avatar";

interface ProfileCardDialogProps {
  characterId: string;
  role: Role;
  avatars: AvatarConfig;
  displayName: string;
  onClose: () => void;
  onEdit: (role: Role) => void;
}

export function ProfileCardDialog({
  characterId,
  role,
  avatars,
  displayName,
  onClose,
  onEdit,
}: ProfileCardDialogProps) {
  const [card, setCard] = useState<ProfileCardData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (role === "assistant" && characterId) {
      request<{ data: Record<string, unknown> }>(`/api/v1/characters/${encodeURIComponent(characterId)}/card`)
        .then((value) => {
          const data = value.data || {};
          setCard({
            name: "assistant",
            identity: { name: data.name, description: data.description },
            personality: { personality: data.personality },
            relationship: { scenario: data.scenario },
            roleplay: {
              first_mes: data.first_mes,
              alternate_greetings: data.alternate_greetings,
              mes_example: data.mes_example,
            },
            revision: 0,
            updated_at: "",
          });
        })
        .catch((reason: Error) => setError(reason.message));
      return;
    }
    const query = role === "user" || !characterId ? "" : `?character_id=${encodeURIComponent(characterId)}`;
    request<ProfileCardData>(`/api/v1/profiles/${role}/card${query}`)
      .then(setCard)
      .catch((reason: Error) => setError(reason.message));
  }, [characterId, role]);

  const blocks: [string, Record<string, unknown>][] = card
    ? [
        ["身份信息", card.identity],
        ["人物性格", card.personality],
        ["角色演绎", card.roleplay || {}],
        ["近期关系", card.relationship],
      ]
    : [];

  return (
    <Modal
      title={`${displayName} · 人物卡`}
      kicker="CHARACTER CARD V2"
      onClose={onClose}
      compact
      footer={role === "user" ? <button className="primary" onClick={() => onEdit(role)}>编辑用户资料</button> : undefined}
    >
      <div className="profile-card-hero">
        <PortraitAvatar role={role} avatars={avatars} label={displayName} />
        <div>
          <h3>{displayName}</h3>
          <p>{role === "assistant" ? "V2 角色卡；基础资料请在角色库编辑。" : "用户设定与偏好"}</p>
        </div>
      </div>
      {error ? (
        <div className="profile-card-empty">{error}</div>
      ) : !card ? (
        <div className="profile-card-empty">正在读取人物关键字段…</div>
      ) : (
        <div className="profile-card-blocks">
          {blocks.map(([title, value]) => (
            <section className="profile-card-block" key={title}>
              <h3>{title}</h3>
              {Object.keys(value).length ? Object.entries(value).map(([key, item]) => (
                <div className="profile-card-row" key={key}>
                  <span>{key}</span>
                  <strong>{friendlyValue(item)}</strong>
                </div>
              )) : <div className="profile-card-empty">暂无记录</div>}
            </section>
          ))}
        </div>
      )}
    </Modal>
  );
}
