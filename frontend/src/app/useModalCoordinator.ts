import { useCallback, useState } from "react";

import type { Role } from "../features/characters";
import { styledConfirm } from "../ui/styledConfirm";
import type { ModalName } from "./viewState";

export function useModalCoordinator() {
  const [modal, setModal] = useState<ModalName>(null);
  const [modalDirty, setModalDirty] = useState(false);
  const [profileCardRole, setProfileCardRole] = useState<Role | null>(null);
  const [profileEditorRole, setProfileEditorRole] = useState<Role | "state">("user");

  const openModal = useCallback((name: Exclude<ModalName, null>) => {
    setModalDirty(false);
    setModal(name);
  }, []);

  const closeModal = useCallback(async () => {
    if (modalDirty && !(await styledConfirm({
      title: "放弃未保存的修改？",
      message: "关闭后，本次尚未保存的编辑会丢失。",
      confirmLabel: "放弃修改",
      danger: true,
    }))) return;
    setModal(null);
    setModalDirty(false);
  }, [modalDirty]);

  return {
    closeModal,
    modal,
    modalDirty,
    openModal,
    profileCardRole,
    profileEditorRole,
    setModal,
    setModalDirty,
    setProfileCardRole,
    setProfileEditorRole,
  };
}
