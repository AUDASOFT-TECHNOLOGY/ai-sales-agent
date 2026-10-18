import { useEffect, type ReactNode } from "react";

/** Marks the document as the Client space while mounted, which switches on its light theme
 *  (`styles/client-theme.css`). The Admin console never mounts it, so it keeps its own look. */
export function ClientTheme({ children }: { children: ReactNode }) {
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.app = "client";
    return () => {
      delete root.dataset.app;
    };
  }, []);
  return <>{children}</>;
}
