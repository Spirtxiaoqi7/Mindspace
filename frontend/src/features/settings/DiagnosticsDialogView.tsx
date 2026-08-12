import { useCallback, useEffect, useState } from "react";

import { request } from "../../shared/api";
import { bool, num, str } from "../../shared/formatters";
import { Modal } from "../../shared/Modal";
import type { DiagnosticReport } from "../../types";
import { styledConfirm } from "../../ui/styledConfirm";

interface DiagnosticsDialogProps {
  onClose: () => void;
  notify: (message: string) => void;
  onCleared: () => void;
}

export function DiagnosticsDialog({
  onClose,
  notify,
  onCleared,
}: DiagnosticsDialogProps) {
  const [report, setReport] = useState<DiagnosticReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [clearing, setClearing] = useState<"knowledge" | "sessions" | "all" | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    request<DiagnosticReport>("/api/v1/diagnostics")
      .then(setReport)
      .catch((error: Error) => notify(error.message))
      .finally(() => setLoading(false));
  }, [notify]);

  useEffect(() => {
    load();
  }, [load]);

  const clear = async (scope: "knowledge" | "sessions" | "all") => {
    if (clearing) return;
    const phrase = {
      knowledge: "CLEAR KNOWLEDGE",
      sessions: "CLEAR SESSIONS",
      all: "CLEAR ALL",
    }[scope];
    if (!(await styledConfirm({
      title: "危险数据操作",
      message: phrase,
      detail: "此操作会清除对应的本地运行数据，无法撤销。",
      confirmLabel: "确认清除",
      danger: true,
    }))) return;
    setClearing(scope);
    try {
      await request("/api/v1/data/clear", {
        method: "POST",
        body: JSON.stringify({ scope, confirmation: phrase }),
      });
      notify("数据清理完成");
      onCleared();
      load();
    } catch (error) {
      notify(`数据清理失败：${(error as Error).message}`);
    } finally {
      setClearing(null);
    }
  };

  return (
    <Modal title="系统诊断与数据管理" kicker="SYSTEM HEALTH" onClose={onClose}>
      {loading ? (
        <div className="empty-mini">正在检查服务状态…</div>
      ) : (
        <>
          <div className="diagnostic-grid">
            <article><span>主服务</span><strong>{report?.ok ? "正常" : "异常"}</strong><small>{str(report?.app.version)}</small></article>
            <article><span>会话</span><strong>{num(report?.counts.sessions)}</strong><small>SQLite 权威存储 · JSON 投影</small></article>
            <article><span>知识块</span><strong>{num(report?.counts.chunks)}</strong><small>{num(report?.counts.characters)} 字符</small></article>
            <article><span>语音</span><strong>{bool(report?.audio.asr_ready) ? "ASR 就绪" : "ASR 降级"}</strong><small>{str(report?.audio.asr_provider)}</small></article>
          </div>
          <details className="report-json">
            <summary>查看完整诊断报告</summary>
            <pre>{JSON.stringify(report, null, 2)}</pre>
          </details>
          <section className="danger-zone">
            <h3>危险数据操作</h3>
            <p>这些操作只影响当前新项目的 runtime，不会修改原 Mindscape 数据。</p>
            <div>
              <button disabled={Boolean(clearing)} onClick={() => void clear("knowledge")}>{clearing === "knowledge" ? "正在清空…" : "清空知识库"}</button>
              <button disabled={Boolean(clearing)} onClick={() => void clear("sessions")}>{clearing === "sessions" ? "正在清空…" : "清空会话"}</button>
              <button className="danger" disabled={Boolean(clearing)} onClick={() => void clear("all")}>{clearing === "all" ? "正在清空…" : "清空全部运行数据"}</button>
            </div>
          </section>
        </>
      )}
    </Modal>
  );
}
