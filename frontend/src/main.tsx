import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./redesign.css";

class RootErrorBoundary extends React.Component<
  React.PropsWithChildren,
  { failed: boolean; message: string }
> {
  state = { failed: false, message: "" };

  static getDerivedStateFromError(error: Error) {
    return { failed: true, message: error.message || "未知界面错误" };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    try {
      localStorage.setItem("mindspace.last-render-error", JSON.stringify({
        timestamp: new Date().toISOString(),
        message: error.message,
        stack: error.stack || "",
        componentStack: info.componentStack || "",
      }));
    } catch {
      // The fallback must remain available even if browser storage is damaged.
    }
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="root-crash-fallback" role="alert">
        <span>MINDSPACE · SAFE RECOVERY</span>
        <h1>聊天界面发生异常</h1>
        <p>会话数据仍保存在本机。你可以重新载入界面，或返回大厅重新选择对话。</p>
        <small>{this.state.message}</small>
        <div>
          <button onClick={() => window.location.reload()}>重新载入</button>
          <button onClick={() => { window.location.hash = "#/modes"; window.location.reload(); }}>
            返回大厅
          </button>
        </div>
      </main>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </React.StrictMode>,
);
