/** Client session state: is a client logged in, as whom. One source of truth so the login page, the
 *  route guard (ClientRoute) and the profile never disagree. Separate from the Admin session. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { demoProfile, resetDemo } from "../demo/demoApi";
import { isDemo, isDemoCredentials, isDemoLogin, setDemo } from "../demo/demoMode";
import { clientSignup } from "./accessApi";
import {
  ClientApiError,
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
  /** Creates a free account; the server signs the new client in, and the profile is read back. */
  signup: (name: string, email: string, password: string, website?: string) => Promise<void>;
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
    // The Demo account never reaches the server: it opens the in-memory demonstration (src/demo).
    if (isDemoLogin(loginName)) {
      if (!isDemoCredentials(loginName, password)) throw new ClientApiError(401, "Invalid credentials.");
      resetDemo();
      setDemo(true);
      setState({ status: "authenticated", profile: demoProfile() });
      return;
    }
    const profile = await clientLogin(loginName, password); // throws ClientApiError on failure
    setState({ status: "authenticated", profile });
  }, []);

  const signup = useCallback(async (name: string, email: string, password: string, website?: string) => {
    await clientSignup({ name, email, password, website }); // throws ClientApiError on failure
    const profile = await clientMe();
    setState({ status: "authenticated", profile });
  }, []);

  const logout = useCallback(async () => {
    if (isDemo()) {
      setDemo(false);
      resetDemo();
      setState({ status: "anonymous", profile: null });
      return;
    }
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
    () => ({ ...state, login, signup, logout, changePassword, updateProfile }),
    [state, login, signup, logout, changePassword, updateProfile],
  );
  return <ClientAuthContext.Provider value={value}>{children}</ClientAuthContext.Provider>;
}

export function useClientAuth(): ClientAuthContextValue {
  const ctx = useContext(ClientAuthContext);
  if (!ctx) throw new Error("useClientAuth must be used within ClientAuthProvider");
  return ctx;
}
