import { useCallback, useEffect, useRef, useState } from "react";
import { clientConversation, clientLive, type ConversationMessage } from "../api";
import type { LiveAnswer } from "./liveModel";

const EVERY_MS = 1000;
const CONVERSATION_EVERY_MS = 2000;
const MESSAGES = 40;

/** Calls `read` again `every` ms after the last answer arrived (never two requests at once), and at once
 *  when the page becomes visible again. Nothing runs while the page is hidden or `read` is null. */
function usePolling<T>(read: (() => Promise<T>) | null, every: number, onValue: (value: T) => void, onError: (error: unknown) => void) {
  const handlers = useRef({ onValue, onError });
  useEffect(() => {
    handlers.current = { onValue, onError };
  });
  useEffect(() => {
    if (!read) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      if (stopped) return;
      if (!document.hidden) {
        try {
          const value = await read();
          if (!stopped) handlers.current.onValue(value);
        } catch (error) {
          if (!stopped) handlers.current.onError(error);
        }
      }
      if (!stopped) timer = setTimeout(run, every);
    };
    const wake = () => {
      if (document.hidden) return;
      if (timer) clearTimeout(timer);
      void run();
    };
    void run();
    document.addEventListener("visibilitychange", wake);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [read, every]);
}

export interface LiveState {
  answer: LiveAnswer | null;
  /** The last read failed: what is shown may be old. */
  lost: boolean;
  /** The server refused the session. */
  signedOut: boolean;
  /** The server's clock now, in ms. Moves every second so that running timers move. */
  serverNow: number;
}

const readLive = () => clientLive();

/** The agent's steps, read every second, and a clock aligned on the server's. */
export function useLive(): LiveState {
  const [answer, setAnswer] = useState<LiveAnswer | null>(null);
  const [lost, setLost] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  const [skew, setSkew] = useState(0);
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  usePolling(
    readLive,
    EVERY_MS,
    (value) => {
      setAnswer(value);
      setSkew(Date.parse(value.at) - Date.now());
      setLost(false);
    },
    (error) => {
      setLost(true);
      if ((error as { status?: number } | null)?.status === 401) setSignedOut(true);
    },
  );
  return { answer, lost, signedOut, serverNow: tick + skew };
}

function sameMessages(a: ConversationMessage[], b: ConversationMessage[]): boolean {
  return a.length === b.length && a.every((m, i) => m.id === b[i]!.id && m.delivery === b[i]!.delivery);
}

/** The messages exchanged with one person, oldest first, read every two seconds while shown. */
export function useFollowedConversation(prospectId: string | null): { messages: ConversationMessage[]; ready: boolean; failed: boolean } {
  const [state, setState] = useState<{ id: string | null; messages: ConversationMessage[]; failed: boolean }>({ id: null, messages: [], failed: false });
  const read = useCallback(
    () => (prospectId ? clientConversation(prospectId, { limit: MESSAGES, offset: 0, order: "desc" }).then((h) => [...h.messages].reverse()) : Promise.resolve([])),
    [prospectId],
  );
  usePolling(
    prospectId ? read : null,
    CONVERSATION_EVERY_MS,
    (messages) => setState((old) => (old.id === prospectId && !old.failed && sameMessages(old.messages, messages) ? old : { id: prospectId, messages, failed: false })),
    () => setState((old) => ({ ...old, failed: true })),
  );
  const current = state.id === prospectId;
  return { messages: current ? state.messages : [], ready: current, failed: state.failed };
}
