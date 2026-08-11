import ReactDOM from "react-dom/client";
import { AppProviders } from "./app/AppProviders";
import { AppRouter } from "./app/AppRouter";
import { AppShell } from "./app/AppShell";
import "./styles.css";
import "./redesign.overrides.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <AppProviders>
    <AppShell>
      <AppRouter />
    </AppShell>
  </AppProviders>,
);
