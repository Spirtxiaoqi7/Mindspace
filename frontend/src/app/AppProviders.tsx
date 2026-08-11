import { StrictMode, type ReactNode } from "react";

export function AppProviders({ children }: { children: ReactNode }) {
  return <StrictMode>{children}</StrictMode>;
}
