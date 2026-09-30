/** Admin session state: is anyone logged in, as whom. One source of truth so the login page and
 *  the route guard (AdminRoute) never disagree about the current status. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { adminLogin, adminLogout, adminMe, type AdminSession } from "./api";

type AdminAuthStatus = "loading" | "authenticated" | "anonymous";

interface AdminAuthState {
  status: AdminAuthStatus;
  admin: AdminSession | null;
}

interface AdminAuthContextValue extends AdminAuthState {
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AdminAuthContext = createContext<AdminAuthContextValue | null>(null);

export function AdminAuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AdminAuthState>({ status: "loading", admin: null });

  useEffect(() => {
    let cancelled = false;
    adminMe()
      .then((admin) => {
        if (!cancelled) setState({ status: "authenticated", admin });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "anonymous", admin: null });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const admin = await adminLogin(username, password); // throws AdminApiError on failure
    setState({ status: "authenticated", admin });
  }, []);

  const logout = useCallback(async () => {
    try {
      await adminLogout();
    } finally {
      // Clear local state even if the request itself failed (network hiccup, already-expired
      // session): the user asked to leave, and the cookie is httpOnly, so this is all we control.
      setState({ status: "anonymous", admin: null });
    }
  }, []);

  const value = useMemo<AdminAuthContextValue>(() => ({ ...state, login, logout }), [state, login, logout]);

  return <AdminAuthContext.Provider value={value}>{children}</AdminAuthContext.Provider>;
}

export function useAdminAuth(): AdminAuthContextValue {
  const ctx = useContext(AdminAuthContext);
  if (!ctx) throw new Error("useAdminAuth must be used within AdminAuthProvider");
  return ctx;
}
