/** Client session state: is a client logged in, as whom. One source of truth so the login page, the
 *  route guard (ClientRoute) and the profile never disagree. Separate from the Admin session. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  clientChangePassword,
  clientLogin,
  clientLogout,
  clientMe,
  clientUpdateProfile,
  type ClientProfile,
  type ProfileChanges,
} from "./api";

type ClientAuthStatus = "loading" | "authenticated" | "anonymous";

interface ClientAuthState {
  status: ClientAuthStatus;
  profile: ClientProfile | null;
}

interface ClientAuthContextValue extends ClientAuthState {
  login: (login: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (current: string, next: string, confirm: string) => Promise<void>;
  updateProfile: (changes: ProfileChanges) => Promise<void>;
}

const ClientAuthContext = createContext<ClientAuthContextValue | null>(null);

export function ClientAuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ClientAuthState>({ status: "loading", profile: null });

  useEffect(() => {
    let cancelled = false;
    clientMe()
      .then((profile) => {
        if (!cancelled) setState({ status: "authenticated", profile });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "anonymous", profile: null });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (loginName: string, password: string) => {
    const profile = await clientLogin(loginName, password); // throws ClientApiError on failure
    setState({ status: "authenticated", profile });
  }, []);

  const logout = useCallback(async () => {
    try {
      await clientLogout();
    } catch {
      /* network hiccup or session already expired: the client still leaves, below */
    }
    setState({ status: "anonymous", profile: null });
  }, []);

  const changePassword = useCallback(async (current: string, next: string, confirm: string) => {
    const profile = await clientChangePassword(current, next, confirm);
    setState({ status: "authenticated", profile });
  }, []);

  const updateProfile = useCallback(async (changes: ProfileChanges) => {
    const profile = await clientUpdateProfile(changes);
    setState({ status: "authenticated", profile });
  }, []);

  const value = useMemo<ClientAuthContextValue>(
    () => ({ ...state, login, logout, changePassword, updateProfile }),
    [state, login, logout, changePassword, updateProfile],
  );
  return <ClientAuthContext.Provider value={value}>{children}</ClientAuthContext.Provider>;
}

export function useClientAuth(): ClientAuthContextValue {
  const ctx = useContext(ClientAuthContext);
  if (!ctx) throw new Error("useClientAuth must be used within ClientAuthProvider");
  return ctx;
}
